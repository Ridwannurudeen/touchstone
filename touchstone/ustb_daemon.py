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
from touchstone.assets import USTB, AssetDescriptor, USTB_ASSET_KEY
from touchstone.controls import AssetState, OperationalEvent
from touchstone.epoch import run_epoch
from touchstone.evaluate import default_controls
from touchstone.evidence import EvidenceStore
from touchstone.publish import asset_key_bytes  # noqa: F401 - re-exported for the CLI
from touchstone.quantities import utc_instant
from touchstone.report import build_observation_report, evidence_references
from touchstone.signing import Ed25519Signer, strict_json_loads
from touchstone.sources import (
    LiveTransport,
    SourceFetchError,
    SourceUnavailable,
    Transport,
)
from touchstone.verify import create_bundle, verify_bundle


# One path segment: no separators, no traversal, no empty name. `.` and `..` match the
# character class, so they are excluded explicitly.
_EPOCH_ID = re.compile(r"(?!\.{1,2}$)[A-Za-z0-9._-]{1,128}")
# Reserved on Windows whatever the extension, and matched case-insensitively.
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


class EpochProductionError(RuntimeError):
    """An epoch could not be produced for a reason that is not a source outage."""


def epoch_id_for(
    scheduled_at: datetime, asset: AssetDescriptor | None = None
) -> str:
    """The epoch a slot at this instant is a statement about.

    One derivation, used by the producer that names the report and by the slot runner that
    asks the registry whether this epoch is already on the chain. A second implementation
    of this would be a second answer to "which day is this", and the two would disagree on
    exactly the boundary the suppression exists for — which is how the asset key came to be
    hashed two different ways and query a registry key that had never existed.

    The prefix comes from the descriptor. It used to be the literal ``ustb-``, which is
    why a second asset could not name its own epoch without colliding with USTB's.
    """
    asset = USTB if asset is None else asset
    return (
        f"{asset.epoch_id_prefix}-"
        f"{utc_instant(scheduled_at, 'scheduled_at').date().isoformat()}"
    )


def make_producer(
    *,
    store: EvidenceStore,
    signer: Ed25519Signer,
    next_sequence: Callable[[], int],
    previous_state: Callable[[date], AssetState],
    transport: Transport | None = None,
    bundle_sink: Callable[[Mapping[str, object]], None] | None = None,
    approval_ledger: bytes | None = None,
    asset: AssetDescriptor | None = None,
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
    there would have been no file to hand them. The bundle is verified before it is handed
    over, so a report this producer returns is one a stranger can check.

    **That covers a fresh slot, not recovery.** A publication left pending by a crash is
    resolved by `OperationsStore.resolve()`, which republishes the stored signed bytes and
    never consults a bundle. For an operation this producer created the bundle is already on
    disk — it is written before the report is returned, and publication happens after — but a
    legacy, corrupted or externally created operation has no such guarantee. An earlier
    version of this docstring claimed publication and verifiability "cannot come apart",
    which recovery disproves.

    ``approval_ledger`` overrides the ledger the slot reads, and is **fixed for the whole
    lifetime of the producer** — it is taken once, here, not per slot. It exists so a test or
    a rehearsal can drive the producer under the control set some historical evidence was
    approved under; a newly compiled control cannot evaluate evidence captured before its
    compile date, so a suite pinned to whatever ships today breaks on every recompile. The
    wiring in ``scripts/run_service.py`` must omit it: production reads the shipped ledger.

    It takes ledger *bytes* rather than resolved controls deliberately. The bytes are the one
    object the controls, the epoch's evaluation, the report's committed digest and the bundle
    are all derived from — hand it a control set instead and a caller could pass one whose
    digest is not the digest the report commits to, which is the four-reads defect below
    wearing a different coat. Nothing here re-checks the override: `build_observation_report`
    already verifies these exact bytes approve these exact controls and hashes them into the
    report, and `create_bundle` refuses bytes whose digest differs from the signed report.
    """
    live = LiveTransport() if transport is None else transport
    frozen_ledger = None if approval_ledger is None else bytes(approval_ledger)
    descriptor = USTB if asset is None else asset

    def produce(scheduled_at: datetime) -> Mapping[str, object] | None:
        moment = utc_instant(scheduled_at, "scheduled_at")
        observed_on = moment.date()
        epoch_id = epoch_id_for(moment, descriptor)

        # The approval ledger is read exactly once per slot, here, and the same bytes reach
        # the controls, the epoch's evaluation, the report's committed digest and the
        # bundle. It used to be read four separate times, and a control declined between
        # two of those reads produced a signed report whose own verifier refused it: the
        # controls came from one ledger and the commitment named another, and every
        # individual check passed because each was consistent with the read next to it.
        ledger = ledger_bytes() if frozen_ledger is None else frozen_ledger
        controls = default_controls(descriptor, ledger_from_bytes(ledger))

        try:
            epoch = run_epoch(
                descriptor,
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
            label = (
                "USTB"
                if descriptor.asset_key == USTB.asset_key
                else descriptor.display_name
            )
            raise SourceUnavailable(
                f"{label} evidence could not be retrieved: {error}"
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
            bundle = create_bundle(
                signed,
                signer.public_key_record(),
                controls,
                evidence_references(epoch),
                approval_ledger=ledger,
            )
            # And verified, not merely built. `create_bundle` checks the fields it derives,
            # but it does not run the verifier: it will happily snapshot a report whose
            # signature does not check out, because signature verification lives in
            # `verify_bundle`. Building successfully was therefore never the same claim as
            # "a reader can verify this", which is the only claim worth publishing behind.
            verify_bundle(bundle)
            bundle_sink(bundle)
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
        sequence = report["sequence"]
        if type(sequence) is not int or sequence < 1:
            # Interpolated into the filename, so a string could carry a separator through.
            raise EpochProductionError(
                f"sequence must be a positive integer to name a bundle: {sequence!r}"
            )
        name = f"{_path_component(report['epoch_id'])}-{sequence}.json"
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
    if (
        isinstance(epoch_id, str)
        and epoch_id.split(".", 1)[0].upper() in _WINDOWS_DEVICES
    ):
        # Windows resolves a reserved device name before the extension and case-insensitively,
        # so `CON.foo`, `NUL.` and `COM1.log` are the console, the null device and a serial
        # port while passing the allowlist below perfectly. Writing a bundle to one discards
        # it or blocks, and the directory afterwards looks like a slot that never produced.
        #
        # Checked on the epoch id rather than on the rendered filename. A rendered-name check
        # was written first and the mutation harness proved it dead: the name is
        # `{epoch_id}-{sequence}.json`, and appending `-1` can never turn a non-device into a
        # device, so it caught nothing this does not. Checking here also refuses a bare `CON`,
        # which the rendered form would allow only because of a suffix this function does not
        # own.
        raise EpochProductionError(
            f"epoch_id names the Windows device {epoch_id.split('.', 1)[0].upper()!r}: "
            f"{epoch_id!r}"
        )
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


def require_verifying_bundle(
    directory: str | os.PathLike[str],
) -> Callable[[object], None]:
    """A ``before_publish`` guard: refuse to republish a report with no verifying bundle.

    A fresh slot writes and verifies its bundle before the report is returned, so publication
    and verifiability go together. Recovery does not: `OperationsStore.resolve()` republishes
    stored signed bytes and never consults a bundle, so a pending operation written before
    bundles existed — or one whose bundle was deleted, truncated or edited — would publish
    anyway. The report then sits on chain permanently with nothing a reader can check it with,
    and the only remedy is a correction.

    The bundle must both verify **and** carry the exact report being republished. A bundle
    that verifies in isolation but describes a different report is the more dangerous of the
    two failures, because everything about it looks correct.
    """
    target = Path(directory)

    def guard(operation: object) -> None:
        report = operation.signed_report["report"]  # type: ignore[attr-defined,index]
        name = f"{_path_component(report['epoch_id'])}-{report['sequence']}.json"
        path = target / name
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise EpochProductionError(
                f"refusing to republish sequence {report['sequence']}: no readable "
                f"verification bundle at {path} ({error}). A report with no bundle cannot "
                "be checked by anyone once it is on chain"
            ) from error
        try:
            # The strict parser, not `json.loads`. Ordinary decoding collapses a duplicate
            # key to its last value, accepts NaN and Infinity, and enforces no size or depth
            # limit — so a bundle edited to carry a duplicate key passed this guard while the
            # offline verifier, handed the same *file*, refused it. The guard's whole purpose
            # is to answer "can a reader verify this file", so it has to read it the way a
            # reader does.
            bundle = strict_json_loads(raw)
        except (ValueError, TypeError) as error:
            raise EpochProductionError(
                f"refusing to republish sequence {report['sequence']}: the bundle at "
                f"{path} is not strictly readable JSON ({error})"
            ) from error
        if not isinstance(bundle, Mapping):
            raise EpochProductionError(
                f"refusing to republish sequence {report['sequence']}: the bundle at "
                f"{path} is not a JSON object"
            )
        verify_bundle(bundle)
        bundled = bundle["signed_report"]
        if bundled != operation.signed_report:  # type: ignore[attr-defined]
            raise EpochProductionError(
                f"refusing to republish sequence {report['sequence']}: the bundle at "
                f"{path} verifies but describes a different report than the one pending"
            )

    return guard
