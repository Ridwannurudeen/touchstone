"""The live USTB epoch producer: the thing the unattended service actually runs.

Every piece of this existed before — retrieval, normalization, evaluation, report
construction, signing, publication — and none of it was connected to a schedule. The
service refused every mode except ``--resolve-only`` and said so honestly, which meant the
registry could be deployed and reachable while the number of autonomous adapters was zero.
This is the function that closes that gap, and it is deliberately small: composition, not
new machinery.

Two rules shape it.

**Silence is recorded as silence.** A source that will not answer produces
:class:`SourceUnavailable`, which the service turns into an incident and publishes nothing.
The asset's state ages toward ``STALE`` on its own. Nothing here ever invents an observation
to fill a slot, because a report that says "we could not look" is worth more than one that
guesses and infinitely more than a gap nobody recorded.

**The sequence comes from the chain, not from a counter.** A local number would drift from
what the registry holds the moment any publication fails or is retried, and the registry
refuses an out-of-order sequence — so the next one is asked for rather than remembered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
import json
import os
from pathlib import Path
import re

from touchstone.approval import ledger_bytes, ledger_from_bytes
from touchstone.controls import AssetState, OperationalEvent
from touchstone.epoch import run_ustb_epoch
from touchstone.evaluate import USTB_ASSET_KEY, default_ustb_controls
from touchstone.evidence import EvidenceStore
from touchstone.publish import asset_key_bytes  # noqa: F401 - re-exported for the CLI
from touchstone.quantities import utc_instant
from touchstone.report import build_observation_report, evidence_references
from touchstone.signing import Ed25519Signer
from touchstone.sources import (
    LiveTransport,
    SourceFetchError,
    SourceUnavailable,
    Transport,
)
from touchstone.verify import create_bundle


# One path segment: no separators, no traversal, no empty name. `.` and `..` match the
# character class, so they are excluded explicitly.
_EPOCH_ID = re.compile(r"(?!\.{1,2}$)[A-Za-z0-9._-]{1,128}")


class EpochProductionError(RuntimeError):
    """An epoch could not be produced for a reason that is not a source outage."""


def epoch_id_for(scheduled_at: datetime) -> str:
    """The epoch a slot at this instant is a statement about.

    One derivation, used by the producer that names the report and by the slot runner that
    asks the registry whether this epoch is already on the chain. A second implementation
    of this would be a second answer to "which day is this", and the two would disagree on
    exactly the boundary the suppression exists for — which is how the asset key came to be
    hashed two different ways and query a registry key that had never existed.
    """
    return f"ustb-{utc_instant(scheduled_at, 'scheduled_at').date().isoformat()}"


def make_producer(
    *,
    store: EvidenceStore,
    signer: Ed25519Signer,
    next_sequence: Callable[[], int],
    previous_state: Callable[[date], AssetState],
    transport: Transport | None = None,
    bundle_sink: Callable[[Mapping[str, object]], None] | None = None,
) -> Callable[[datetime], Mapping[str, object] | None]:
    """Build the ``produce`` callable the service's slot runner expects.

    The dependencies are injected rather than constructed here so the whole path can be
    driven end to end in a test with a fixture transport and a fake chain — the alternative
    is a producer that can only be exercised against the live internet, which is a producer
    nobody exercises.

    ``bundle_sink`` receives the offline verification bundle for each report, before that
    report is returned for publication. Until it existed, `create_bundle` had exactly one
    caller — the local-chain rehearsal in `scripts/e2e_local.py` — so an unattended run
    published a signed report to the registry and produced nothing a reader could verify it
    with. The dossier's whole claim is that a stranger can check the result offline, and
    there would have been no file to hand them.
    """
    live = LiveTransport() if transport is None else transport

    def produce(scheduled_at: datetime) -> Mapping[str, object] | None:
        moment = utc_instant(scheduled_at, "scheduled_at")
        observed_on = moment.date()
        epoch_id = epoch_id_for(moment)

        # The approval ledger is read exactly once per slot, here, and the same bytes reach
        # the controls, the epoch's evaluation, the report's committed digest and the
        # bundle. It used to be read four separate times, and a control declined between
        # two of those reads produced a signed report whose own verifier refused it: the
        # controls came from one ledger and the commitment named another, and every
        # individual check passed because each was consistent with the read next to it.
        ledger = ledger_bytes()
        controls = default_ustb_controls(ledger_from_bytes(ledger))

        try:
            epoch = run_ustb_epoch(
                transport=live,
                store=store,
                now=observed_on,
                retrieved_at=moment,
                controls=controls,
            )
        except SourceFetchError as error:
            # The one failure that is emphatically *not* a statement about the asset. It is
            # raised as the service's own source type so the slot records an outage and
            # publishes nothing, rather than an epoch failure that reads like a finding.
            raise SourceUnavailable(
                f"USTB evidence could not be retrieved: {error}"
            ) from error

        report = build_observation_report(
            epoch,
            controls,
            epoch_id=epoch_id,
            sequence=next_sequence(),
            publisher_kid=signer.kid,
            previous_state=previous_state(observed_on),
            event=OperationalEvent.RECONFIRMED,
            approval_ledger=ledger,
        )
        signed = signer.sign_report(report)
        if bundle_sink is not None:
            # Deliberately before the return, so the report the service is about to publish
            # is one that could be bundled. A bundle failure after publication would leave
            # an unverifiable report permanently on chain, correctable only by a new one.
            bundle_sink(
                create_bundle(
                    signed,
                    signer.public_key_record(),
                    controls,
                    evidence_references(epoch),
                    approval_ledger=ledger,
                )
            )
        return signed

    return produce


def write_bundle(
    directory: str | os.PathLike[str],
) -> Callable[[Mapping[str, object]], None]:
    """A ``bundle_sink`` that persists each bundle under ``directory``.

    Named from the report it describes rather than the wall clock, so the same report always
    lands on the same path and a retried slot overwrites its own file instead of accumulating
    near-duplicates nobody can tell apart. Written to a temporary name and replaced, because
    a bundle truncated by a crash mid-write is a file that looks present and fails
    verification — worse than an absent one, which at least reads as absent.

    **The epoch id is validated before it becomes a path.** It arrives from the report, and
    the report builder checks only that it is non-empty text — so `epoch_id="../escaped"`
    wrote outside the bundle directory entirely. The wired service derives its epoch ids from
    `epoch_id_for`, which cannot produce one, but this sink is a reusable public function and
    a caller is not obliged to be that careful.

    Not a power-loss guarantee: neither the file nor the directory is fsynced, and a crash
    between the write and the replace can leave a `.partial` behind. Both are acceptable for
    an artifact rebuildable from the evidence store and the ledger it was signed under; the
    thing that must not happen is a *present* bundle that fails verification, and `os.replace`
    is what prevents that.
    """
    target = Path(directory)

    def sink(bundle: Mapping[str, object]) -> None:
        report = bundle["signed_report"]["report"]  # type: ignore[index]
        name = f"{_path_component(report['epoch_id'])}-{report['sequence']}.json"
        target.mkdir(parents=True, exist_ok=True)
        destination = target / name
        staging = destination.with_suffix(".json.partial")
        staging.write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staging, destination)

    return sink


def _path_component(epoch_id: object) -> str:
    """One path segment, or a refusal. Never a traversal.

    Deliberately an allowlist. A denylist of `..` and separators invites the next encoding
    that was not thought of, and an epoch id has no legitimate reason to contain anything
    outside this set.
    """
    if not isinstance(epoch_id, str) or not _EPOCH_ID.fullmatch(epoch_id):
        raise EpochProductionError(
            f"epoch_id is not usable as a filename: {epoch_id!r}. It must be "
            "letters, digits, dots, dashes or underscores, and cannot be '.' or '..'"
        )
    return epoch_id


def report_uri(signed_report: Mapping[str, object]) -> str:
    """A stable name for a published report, derived from what it says.

    Deliberately not a URL. Nothing is hosted yet, and minting a `https://` URI that
    resolves to nothing would put a promise on chain that the project has not kept.
    """
    report = signed_report["report"]
    if not isinstance(report, Mapping):
        raise EpochProductionError("a signed report must carry its report")
    return f"urn:touchstone:ustb:{report['epoch_id']}:{report['sequence']}"


def asset_key() -> str:
    return USTB_ASSET_KEY
