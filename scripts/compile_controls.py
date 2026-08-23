"""Compile control candidates from committed evidence, once, for human approval.

This is the only place a model is ever called. It runs at control-proposal time, on the
owner's machine, and produces the compilation artifacts that approved controls pin by
digest. Nothing in the serving runtime reads a model: a daily report that depended on a
service which can change its mind between epochs would be a durable record built on a
moving one.

What it does not do is approve anything. It prints what the compiler accepted and stops.
Approval is a human act, and the only two things it may change on a candidate are its
``approval_state`` and the ``compilation_sha256`` naming the artifact it came from — any
other edit makes a different control that this compiler never proposed.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.compiler import (  # noqa: E402
    CompilationStatus,
    FOBXX_EXCERPT_LIMIT,
    HTTPProvider,
    compile_evidence,
)
from touchstone.assets import FOBXX, USTB, AssetDescriptor  # noqa: E402
from touchstone.epoch import (  # noqa: E402
    FIXTURE_RETRIEVED_AT,
    FixtureTransport,
    run_epoch,
    run_ustb_epoch,
)
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.sources import LiveTransport, TransportResponse  # noqa: E402


COMPILATIONS = ROOT / "data" / "compilations"
# The capture the approved control set was written against, and the one whose evidence the
# compiler is shown. Seeded with the day before, because a value control observes a row
# confirmed across two captures and the store must already hold the earlier one.
SEED_CAPTURE = date(2026, 8, 13)
COMPILE_CAPTURE = date(2026, 8, 14)
FOBXX_CAPTURE = date(2026, 8, 22)
FOBXX_RETRIEVED_AT = datetime(2026, 8, 22, 3, 4, 44, 564845, tzinfo=timezone.utc)
ASSET_BY_NAME = {"ustb": USTB, "fobxx": FOBXX}


class FobxxFixtureTransport:
    """Serve the four retained FOBXX captures through their declared requests."""

    def __init__(self, fixtures: Path) -> None:
        self.fixtures = fixtures.resolve()

    def get(self, url: str, *, timeout: float, max_bytes: int) -> TransportResponse:
        del timeout
        if url == FOBXX.sources[2].url:
            fixture = "fobxx-submissions-20260815.json"
            content_type = "application/json"
        elif url == FOBXX.sources[3].url:
            fixture = "fobxx-nmfp3-20260731.xml"
            content_type = "text/xml"
        else:
            raise ValueError("fixture transport received an unregistered GET URL")
        return self._response(fixture, content_type, max_bytes)

    def post(
        self, url: str, body: bytes, *, timeout: float, max_bytes: int
    ) -> TransportResponse:
        del timeout
        if url != FOBXX.sources[0].url:
            raise ValueError("fixture transport received an unregistered POST URL")
        if body == FOBXX.sources[0].request_body:
            fixture = "fobxx-product-lookup-20260822.json"
        elif b"PricesHistoryFOBXX" in body:
            fixture = "fobxx-price-history-90d-20260822.json"
        else:
            raise ValueError("fixture transport received an unregistered POST body")
        return self._response(fixture, "application/json", max_bytes)

    def _response(
        self, fixture: str, content_type: str, max_bytes: int
    ) -> TransportResponse:
        raw = (self.fixtures / fixture).read_bytes()
        if len(raw) > max_bytes:
            raise ValueError("committed fixture exceeds source manifest byte cap")
        return TransportResponse(
            status_code=200,
            headers={"Content-Type": content_type},
            body=raw,
        )


def compile_all(
    *,
    asset: AssetDescriptor,
    live: bool,
    fixtures: Path,
    source_user_agent: str | None = None,
) -> dict[str, str]:
    """Compile every source for one selected asset and persist each artifact."""
    workspace = Path(tempfile.mkdtemp(prefix="touchstone-compile-"))
    store = EvidenceStore(workspace / "evidence")
    if live:
        retrieved_at = datetime.now(timezone.utc)
        epoch = run_epoch(
            asset,
            transport=LiveTransport(user_agent=source_user_agent),
            store=store,
            now=retrieved_at.date(),
            retrieved_at=retrieved_at,
            controls=(),
        )
    elif asset is USTB:
        run_ustb_epoch(
            transport=FixtureTransport(fixtures, SEED_CAPTURE),
            store=store,
            now=SEED_CAPTURE,
            retrieved_at=FIXTURE_RETRIEVED_AT[SEED_CAPTURE],
        )
        retrieved_at = FIXTURE_RETRIEVED_AT[COMPILE_CAPTURE]
        epoch = run_ustb_epoch(
            transport=FixtureTransport(fixtures, COMPILE_CAPTURE),
            store=store,
            now=COMPILE_CAPTURE,
            retrieved_at=retrieved_at,
        )
    elif asset is FOBXX:
        retrieved_at = FOBXX_RETRIEVED_AT
        epoch = run_epoch(
            FOBXX,
            transport=FobxxFixtureTransport(fixtures),
            store=store,
            now=FOBXX_CAPTURE,
            retrieved_at=retrieved_at,
            controls=(),
        )
    else:
        raise ValueError("asset is not supported by the compilation entry point")

    evidence_by_source = {
        source.source_id: source.evidence_sha256 for source in epoch.sources
    }
    # Far above the provider's 30s default. This runs once, by hand, and a current model
    # thinks before it answers — the default timed out mid-response on the second source,
    # which costs the call and proves nothing about the endpoint.
    provider = HTTPProvider(timeout=600.0)
    COMPILATIONS.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}

    for manifest in asset.sources:
        comparison_evidence = None
        if asset is FOBXX and manifest is FOBXX.sources[1]:
            comparison_evidence = {
                FOBXX.sources[3].source_id: evidence_by_source[
                    FOBXX.sources[3].source_id
                ]
            }
        elif asset is FOBXX and manifest is FOBXX.sources[3]:
            comparison_evidence = {
                FOBXX.sources[2].source_id: evidence_by_source[
                    FOBXX.sources[2].source_id
                ]
            }
        print(f"\n=== {manifest.source_id} ===", flush=True)
        result = compile_evidence(
            provider,
            evidence_sha256=evidence_by_source[manifest.source_id],
            source_manifest=manifest,
            store=store,
            retrieved_at=retrieved_at,
            excerpt_limit=(
                FOBXX_EXCERPT_LIMIT
                if asset is FOBXX and manifest is FOBXX.sources[3]
                else 8_192
            ),
            asset=asset,
            comparison_evidence_sha256=comparison_evidence,
        )
        # The stored object's exact bytes, copied rather than re-serialised: the digest is
        # over these bytes and a round trip through json would be a different file that no
        # longer hashes to the name it is filed under.
        source_path = store.objects_dir / result.compilation_sha256
        shutil.copyfile(source_path, COMPILATIONS / f"{result.compilation_sha256}.json")
        digests[manifest.source_id] = result.compilation_sha256
        print(f"artifact: {result.compilation_sha256}")
        for outcome in result.outcomes:
            print(f"  {outcome.status.value}: {outcome.reason}")
            if outcome.status is CompilationStatus.ACCEPTED and outcome.control:
                print(
                    json.dumps(outcome.control.to_mapping(), indent=4, sort_keys=True)
                )
    return digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset",
        choices=tuple(ASSET_BY_NAME),
        default="ustb",
        help="compile one asset only (default: ustb)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="retrieve evidence from the issuer instead of the committed capture",
    )
    parser.add_argument("--fixtures", default=str(ROOT / "fixtures"))
    parser.add_argument(
        "--source-user-agent",
        help="identifying User-Agent with contact email required for live SEC retrieval",
    )
    arguments = parser.parse_args(argv)

    digests = compile_all(
        asset=ASSET_BY_NAME[arguments.asset],
        live=arguments.live,
        fixtures=Path(arguments.fixtures),
        source_user_agent=arguments.source_user_agent,
    )
    print("\n=== artifacts written ===")
    print(json.dumps(digests, indent=2, sort_keys=True))
    print(
        "\nNothing is approved. Review each accepted candidate, then attach its "
        "artifact digest to the control it approves."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
