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
    HTTPProvider,
    compile_evidence,
)
from touchstone.epoch import FIXTURE_RETRIEVED_AT, FixtureTransport, run_ustb_epoch  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.sources import USTB_SOURCES, LiveTransport  # noqa: E402


COMPILATIONS = ROOT / "data" / "compilations"
# The capture the approved control set was written against, and the one whose evidence the
# compiler is shown. Seeded with the day before, because a value control observes a row
# confirmed across two captures and the store must already hold the earlier one.
SEED_CAPTURE = date(2026, 8, 13)
COMPILE_CAPTURE = date(2026, 8, 14)


def compile_all(*, live: bool, fixtures: Path) -> dict[str, str]:
    """Compile every USTB source and persist each artifact. Returns source -> digest."""
    workspace = Path(tempfile.mkdtemp(prefix="touchstone-compile-"))
    store = EvidenceStore(workspace / "evidence")
    if live:
        retrieved_at = datetime.now(timezone.utc)
        epoch = run_ustb_epoch(
            transport=LiveTransport(),
            store=store,
            now=retrieved_at.date(),
            retrieved_at=retrieved_at,
        )
    else:
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

    evidence_by_source = {
        source.source_id: source.evidence_sha256 for source in epoch.sources
    }
    # Far above the provider's 30s default. This runs once, by hand, and a current model
    # thinks before it answers — the default timed out mid-response on the second source,
    # which costs the call and proves nothing about the endpoint.
    provider = HTTPProvider(timeout=600.0)
    COMPILATIONS.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}

    for manifest in USTB_SOURCES:
        print(f"\n=== {manifest.source_id} ===", flush=True)
        result = compile_evidence(
            provider,
            evidence_sha256=evidence_by_source[manifest.source_id],
            source_manifest=manifest,
            store=store,
            retrieved_at=retrieved_at,
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
        "--live",
        action="store_true",
        help="retrieve evidence from the issuer instead of the committed capture",
    )
    parser.add_argument("--fixtures", default=str(ROOT / "fixtures"))
    arguments = parser.parse_args(argv)

    digests = compile_all(live=arguments.live, fixtures=Path(arguments.fixtures))
    print("\n=== artifacts written ===")
    print(json.dumps(digests, indent=2, sort_keys=True))
    print(
        "\nNothing is approved. Review each accepted candidate, then attach its "
        "artifact digest to the control it approves."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
