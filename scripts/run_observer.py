"""Watch the issuer's sources on a short cadence. Sign nothing, publish nothing.

The daily service makes one statement per day. This makes none. It fetches each allowlisted
source, stores the exact response bytes in the same evidence store the service reads, and
records what changed since the last look.

**It is given no key material and contains no publishing code.** Nothing below imports a
signer, a publisher, a deployment manifest or a registry, and a test parses the import graph
to keep that true. The process that runs most often is the one that carries the least.

That is a statement about this program, not a capability boundary around it. Two things bound
how far it can be taken:

* **Key exposure** is bounded by running as a separate Unix identity, because same-UID code
  can otherwise read the publisher's process environment. That is done; see
  `docs/DEPLOY-SERVICE.md`.
* **Conclusion integrity is not bounded at all.** This process writes the evidence store the
  daily service confirms against, and `retrieved_at` is caller-supplied. Code running here
  could append a fabricated payload with a backdated capture time and cause a later epoch to
  confirm a value that was never retrieved. It cannot publish; it can decide what a
  publication concludes. That is **R-13** in `docs/THREAT-MODEL.md`, and it is the reason
  "the process that runs most often is the one that can do least" is a description of this
  file's imports rather than a security property.

Two things it deliberately does not do:

* **It never opens an epoch.** A source changing is not a reason to publish; the confirmation
  rule decides that, a day at a time, in the service.
* **It never retries a fetch inside a slot.** A source that will not answer is recorded as
  unavailable and looked at again next slot. Retrying inside the slot would turn one failing
  source into a burst against an issuer this project does not operate.

Run it as::

    python scripts/run_observer.py --workspace <dir> [--interval-seconds 900]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import asdict  # noqa: E402

from touchstone import observation  # noqa: E402
from touchstone.assets import USTB  # noqa: E402
from touchstone.evidence import EvidenceStore  # noqa: E402
from touchstone.locking import LockUnavailable, exclusive_lock  # noqa: E402
from touchstone.schedule import run_schedule  # noqa: E402
from touchstone.sources import (  # noqa: E402
    LiveTransport,
    SourceFetchError,
    SourceUnavailable,
    fetch_source,
)
from touchstone.workspace import Workspace  # noqa: E402


def normalized_digest(source_id: str, raw: bytes) -> str | None:
    """Digest of the normalized observation, or ``None`` if it would not normalize."""
    try:
        parsed = USTB.normalize(source_id, raw)
    except Exception:
        # Deliberately broad, and deliberately not re-raised. A normalizer refusing a
        # payload is an observation about the source, not a failure of the watcher, and a
        # watcher that exits on it stops watching the other sources too.
        return None
    return observation.canonical_digest(asdict(parsed))


def look_once(
    *,
    store: EvidenceStore,
    transport: object,
    log_path: Path,
    now: datetime,
) -> list[observation.Observation]:
    """One pass over every source. Each source is recorded independently."""
    previous = observation.latest_by_source(log_path)
    seen: list[observation.Observation] = []

    for manifest in USTB.sources:
        source_id = manifest.source_id
        prior = previous.get(source_id, {})
        prior_payload = prior.get("payload_sha256")
        prior_normalized = prior.get("normalized_sha256")

        payload_sha256: str | None = None
        normalized_sha256: str | None = None
        byte_size: int | None = None
        detail: str | None = None
        failed = False

        try:
            result = fetch_source(
                source_id,
                store=store,
                transport=transport,
                manifest=manifest,
                retrieved_at=now,
            )
            payload_sha256 = result.evidence_sha256
            byte_size = result.byte_size
        except (SourceUnavailable, SourceFetchError, OSError) as error:
            failed = True
            detail = f"{type(error).__name__}: {error}"

        if payload_sha256 is not None:
            if payload_sha256 == prior_payload:
                # Same bytes, so the normalized form is the same by construction. Re-parsing
                # to prove that would burn CPU to reach a conclusion the digest already has.
                normalized_sha256 = prior_normalized
            else:
                raw = (store.objects_dir / payload_sha256).read_bytes()
                normalized_sha256 = normalized_digest(source_id, raw)
                if normalized_sha256 is None:
                    detail = "artifact stored, normalizer refused it"

        transition = observation.classify(
            payload_sha256=payload_sha256,
            previous_payload_sha256=prior_payload,
            normalized_sha256=normalized_sha256,
            previous_normalized_sha256=prior_normalized,
            failed=failed,
        )
        entry = observation.Observation(
            source_id=source_id,
            observed_at=observation.stamp(now),
            transition=transition,
            payload_sha256=payload_sha256,
            previous_payload_sha256=prior_payload,
            normalized_sha256=normalized_sha256,
            previous_normalized_sha256=prior_normalized,
            byte_size=byte_size,
            detail=detail,
        )
        observation.append(log_path, observation.build_record(entry))
        seen.append(entry)

    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=observation.DEFAULT_INTERVAL_SECONDS,
        help=(
            "seconds between passes. Refused below "
            f"{observation.MINIMUM_INTERVAL_SECONDS:.0f}s: the sources belong to a fund "
            "issuer this project does not operate."
        ),
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="stop after this many passes. Omit to watch until stopped.",
    )
    arguments = parser.parse_args(argv)

    try:
        interval = observation.validate_interval(arguments.interval_seconds)
    except (TypeError, ValueError) as error:
        print(f"OBSERVER FAIL: {error}", file=sys.stderr)
        return 1

    workspace = Workspace(arguments.workspace)
    workspace.root.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(workspace.evidence)
    transport = LiveTransport()
    log_path = workspace.observation_log

    def job(scheduled_at: datetime) -> None:
        for entry in look_once(
            store=store, transport=transport, log_path=log_path, now=scheduled_at
        ):
            print(
                f"{entry.observed_at}  {entry.source_id:<28} {entry.transition.value}"
                + (f"  ({entry.detail})" if entry.detail else ""),
                flush=True,
            )

    print(
        f"observing {len(USTB.sources)} sources every {interval:.0f}s into {log_path}",
        flush=True,
    )
    try:
        with exclusive_lock(workspace.observer_lock):
            run_schedule(
                job,
                interval_seconds=interval,
                max_runs=arguments.max_runs,
                now=lambda: datetime.now(timezone.utc),
            )
    except LockUnavailable:
        print("OBSERVER FAIL: another observer holds this workspace", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
