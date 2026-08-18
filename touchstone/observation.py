"""Observation-only surveillance: watch the sources, decide nothing.

The epoch service makes one statement per day because an epoch is a statement about a day's
evidence, and the registry enforces that. Watching the issuer, though, has nothing to do with
that cadence — a source can change at any hour, and a system that only looks once a day cannot
say when it changed, only that it differs from yesterday.

So this records observations and **never** evaluates, signs, publishes, or opens an epoch.
Nothing here imports a signer, a publisher or a registry, and nothing here decides whether an
asset is verified. That separation is the point: the moment a watcher can also publish, "the
bytes changed" and "there is a new report to make" become one decision, and the confirmation
rule that makes a report worth anything is the thing that gets bypassed.

**Captures land in the same evidence store the daily service reads.** That is safe because
``EvidenceStore.store`` takes its own exclusive lock around verify-then-append, which is a
different lock from the workspace lock the service holds. It is also the point: a value
control observes only a row confirmed by a capture at least ``CONFIRMATION_INTERVAL_SECONDS``
earlier, so the daily slot could previously only confirm against *the previous day's slot*.
When those two slots landed 23.66 hours apart the whole epoch abstained, for no reason but
timing. A watcher capturing through the day means a qualifying predecessor almost always
exists.

## Why "changed" is three answers and not one

Reporting "the source changed" whenever the response bytes differ would overclaim. A feed can
re-serialise, reorder, or restate a field that no control reads. The transitions below
separate the raw payload from the normalized observation, so a reader can tell a presentation
change from a substantive one, and neither is described as the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from touchstone.locking import exclusive_lock
from touchstone.quantities import finite_positive, utc_instant

OBSERVATION_VERSION = "touchstone.observation.v1"

# A floor, not a default. The interval is an operator's choice, but a misconfiguration that
# sends it to one second would turn a watcher into a load generator against a fund issuer
# nobody here operates, and the first anyone would know is a block. The default is far above
# this; the floor exists so the mistake is impossible rather than unlikely.
MINIMUM_INTERVAL_SECONDS = 300.0
DEFAULT_INTERVAL_SECONDS = 900.0


class Transition(str, Enum):
    """What one observation says relative to the one before it, for the same source."""

    FIRST_OBSERVATION = "FIRST_OBSERVATION"
    UNCHANGED = "UNCHANGED"
    # Bytes differ, normalized observation does not. A re-serialisation, a reordering, a
    # field no adapter reads. Recorded, and deliberately not called a change in the data.
    PAYLOAD_CHANGED = "PAYLOAD_CHANGED"
    # The normalized observation itself differs. This is the one that means something.
    OBSERVATION_CHANGED = "OBSERVATION_CHANGED"
    # The fetch did not produce an artifact. Silence, recorded as silence.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    # An artifact arrived and could not be normalized. Distinct from unavailability,
    # because a source that answers with something unparseable is a different failure from
    # a source that does not answer, and they want different attention.
    PARSE_FAILED = "PARSE_FAILED"


@dataclass(frozen=True, slots=True)
class Observation:
    """One recorded look at one source."""

    source_id: str
    observed_at: str
    transition: Transition
    payload_sha256: str | None
    previous_payload_sha256: str | None
    normalized_sha256: str | None
    previous_normalized_sha256: str | None
    byte_size: int | None
    detail: str | None


def classify(
    *,
    payload_sha256: str | None,
    previous_payload_sha256: str | None,
    normalized_sha256: str | None,
    previous_normalized_sha256: str | None,
    failed: bool = False,
) -> Transition:
    """Decide what this observation says, from digests alone.

    Kept free of I/O so the decision can be exercised directly. The order matters: an
    unavailable source is not an unchanged one, and a payload that cannot be normalized is
    not evidence that the observation is unchanged either — in both cases the comparison
    the caller wants simply did not happen, and saying ``UNCHANGED`` would assert it did.
    """
    if failed or payload_sha256 is None:
        return Transition.SOURCE_UNAVAILABLE
    if previous_payload_sha256 is None:
        return Transition.FIRST_OBSERVATION
    if payload_sha256 == previous_payload_sha256:
        return Transition.UNCHANGED
    if normalized_sha256 is None:
        return Transition.PARSE_FAILED
    if previous_normalized_sha256 is None:
        # The bytes moved and there is nothing comparable behind them. Reporting
        # PAYLOAD_CHANGED would imply the substance was checked and found equal.
        return Transition.OBSERVATION_CHANGED
    if normalized_sha256 == previous_normalized_sha256:
        return Transition.PAYLOAD_CHANGED
    return Transition.OBSERVATION_CHANGED


def canonical_digest(value: object) -> str:
    """A stable digest for a normalized observation.

    ``default=str`` renders the ``Decimal`` and ``date`` a normalized observation is made of
    without going through ``float``, which would make the digest depend on binary rounding
    rather than on the value the issuer published.
    """
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_record(
    observation: Observation, *, now: datetime | None = None
) -> dict[str, Any]:
    """Render one observation as the line that will be appended."""
    del now  # the instant is the observation's own; a second clock read would disagree
    if not isinstance(observation.source_id, str) or not observation.source_id:
        raise ValueError("source_id must be non-empty text")
    return {
        "byte_size": observation.byte_size,
        "detail": observation.detail,
        "normalized_sha256": observation.normalized_sha256,
        "observed_at": observation.observed_at,
        "payload_sha256": observation.payload_sha256,
        "previous_normalized_sha256": observation.previous_normalized_sha256,
        "previous_payload_sha256": observation.previous_payload_sha256,
        "source_id": observation.source_id,
        "transition": observation.transition.value,
        "version": OBSERVATION_VERSION,
    }


def append(path: str | os.PathLike[str], record: dict[str, Any]) -> None:
    """Append one observation, under the same lock every writer takes.

    The log is append-only and flushed to disk before the call returns, because the reason
    to keep it is to be able to say what was true at a moment that has already passed, and a
    record still in a buffer when the process dies cannot say anything.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with exclusive_lock(target):
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def read_all(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Every observation recorded, oldest first. Missing log reads as no observations."""
    target = Path(path)
    if not target.exists():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def latest_by_source(path: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    """The most recent observation for each source, by position in the log.

    Ordered by position rather than by ``observed_at`` deliberately: the log is append-only,
    so position is what actually happened, while a timestamp is a claim a record makes about
    itself and can go backwards if a clock does.
    """
    latest: dict[str, dict[str, Any]] = {}
    for record in read_all(path):
        source_id = record.get("source_id")
        if isinstance(source_id, str) and source_id:
            latest[source_id] = record
    return latest


def validate_interval(seconds: float) -> float:
    """Refuse an interval that would treat a third-party issuer as a load target."""
    value = finite_positive(seconds, "interval_seconds")
    if value < MINIMUM_INTERVAL_SECONDS:
        raise ValueError(
            f"interval {value}s is below the {MINIMUM_INTERVAL_SECONDS}s floor; the "
            "sources belong to a fund issuer this project does not operate"
        )
    return value


def stamp(moment: datetime) -> str:
    """One spelling of an instant, so two records can be compared as text."""
    return utc_instant(moment, "observed_at").isoformat().replace("+00:00", "Z")
