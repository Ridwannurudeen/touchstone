"""The binding between an approved control and the compilation that produced it.

Compilation and evaluation used to be two disconnected paths. The compiler validated a
candidate's span, bindings and confidence and emitted a ``proposed`` record; evaluation
admitted any record whose ``approval_state`` happened to read ``approved``, and nothing
required that record to have come from a compilation, to match one, or to be reachable from
any provenance digest. A report carried ``compiler_provenance_digests`` that the report
builder and the offline verifier checked only as well-formed hex. The compiler's work was
therefore advisory to whoever curated the control set, and the "AI proposes, deterministic
systems decide" separation rested entirely on that curator.

This module is the binding. An approved control names the artifact it came from; the
artifact is resolved, hashed, and searched for the exact candidate; and the approved record
is required to differ from that candidate in exactly two fields — ``approval_state`` and
``compilation_sha256``. Anything else is a different control that no compiler proposed.

The ledger also records what a human *declined*. A candidate that passed every deterministic
gate and was still not approved is a decision someone made, and a control set that silently
omits it cannot be audited for why.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path

from touchstone.controls import ControlRecord


ROOT = Path(__file__).parents[1]
COMPILATIONS = ROOT / "data" / "compilations"
LEDGER = ROOT / "data" / "compilations" / "APPROVALS.json"
LEDGER_VERSION = "touchstone.approval-ledger.v1"
APPROVED_KEY = "approved"
DECLINED_KEY = "declined"

# The only two fields approval may touch. Everything else is what the compiler proposed.
_APPROVAL_FIELDS = frozenset({"approval_state", "compilation_sha256"})


class ApprovalError(RuntimeError):
    """An approved control cannot be resolved to the compilation that produced it."""


def ledger_bytes(path: str | Path = LEDGER) -> bytes:
    """The ledger's exact bytes, for hashing and for carrying in a bundle."""
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise ApprovalError(f"the approval ledger cannot be read: {error}") from error


def ledger_digest(path: str | Path = LEDGER) -> str:
    """The digest a report commits to, so a reader can tell which ledger it meant."""
    return hashlib.sha256(ledger_bytes(path)).hexdigest()


def assert_ledger_permits(controls, ledger: Mapping) -> None:
    """Every reported control approved exactly once, and none of them declined.

    Without this an offline reader can confirm a control is exactly what a compilation
    accepted — and still not know whether a human refused it. The compiler accepted ten
    candidates; two were declined. Both remain in their artifacts, because an artifact
    records what the compiler did, not what a person decided afterwards.
    """
    approved = [e for e in ledger[APPROVED_KEY] if isinstance(e, Mapping)]
    declined = [e for e in ledger[DECLINED_KEY] if isinstance(e, Mapping)]
    for control in controls:
        pair = (control.control_id, control.compilation_sha256)
        refused = [
            e
            for e in declined
            if (e.get("control_id"), e.get("compilation_sha256")) == pair
        ]
        if refused:
            reason = refused[0].get("reason", "no reason recorded")
            raise ApprovalError(
                f"{control.control_id!r} was declined: {reason}"
            )
        matches = [
            e
            for e in approved
            if (e.get("control_id"), e.get("compilation_sha256")) == pair
        ]
        if len(matches) != 1:
            raise ApprovalError(
                f"{control.control_id!r} appears {len(matches)} times in the approved "
                "ledger; exactly one approval is required"
            )


def ledger_from_bytes(raw: bytes) -> Mapping[str, list]:
    """Parse and validate a ledger carried in a bundle, with no filesystem involved."""
    try:
        ledger = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError(f"the approval ledger is not readable JSON: {error}") from error
    return _validated_ledger(ledger)


def _validated_ledger(ledger: object) -> Mapping[str, list]:
    """One shape check, shared by the on-disk and in-bundle readers."""
    if not isinstance(ledger, Mapping) or ledger.get("version") != LEDGER_VERSION:
        raise ApprovalError("the approval ledger is not a supported version")
    for key in (APPROVED_KEY, DECLINED_KEY):
        if not isinstance(ledger.get(key), list):
            raise ApprovalError(f"the approval ledger has no {key} list")
    return ledger


def load_approval_ledger(path: str | Path = LEDGER) -> Mapping[str, list]:
    """Read the committed record of what was approved and what was declined."""
    location = Path(path)
    try:
        ledger = json.loads(location.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ApprovalError(f"no approval ledger at {location}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ApprovalError(f"the approval ledger cannot be read: {error}") from error
    return _validated_ledger(ledger)


def compilation_from_bytes(digest: str, raw: bytes) -> Mapping:
    """Prove these bytes are the artifact ``digest`` names, and read them.

    The hash is over the bytes as they arrived, never over a re-serialisation. A bundle
    that carried a pretty-printed copy of an artifact would hash to something else and be
    refused, which is the point: the digest is a claim about exact bytes.
    """
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise ApprovalError(
            f"compilation {digest} hashes to {actual}; it is not the artifact named"
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError(
            f"compilation {digest} is not readable JSON: {error}"
        ) from error


def load_compilation(digest: str, *, directory: str | Path = COMPILATIONS) -> Mapping:
    """Read one committed compilation artifact from disk, hash-checked."""
    location = Path(directory) / f"{digest}.json"
    try:
        raw = location.read_bytes()
    except OSError as error:
        raise ApprovalError(f"compilation {digest} is missing: {error}") from error
    return compilation_from_bytes(digest, raw)


def compilation_bytes(digest: str, *, directory: str | Path = COMPILATIONS) -> bytes:
    """The artifact's exact bytes, for embedding in a bundle."""
    location = Path(directory) / f"{digest}.json"
    try:
        raw = location.read_bytes()
    except OSError as error:
        raise ApprovalError(f"compilation {digest} is missing: {error}") from error
    compilation_from_bytes(digest, raw)
    return raw


def from_directory(directory: str | Path = COMPILATIONS):
    """A resolver that reads artifacts from a committed directory."""

    def resolve(digest: str) -> Mapping:
        return load_compilation(digest, directory=directory)

    return resolve


def from_mapping(artifacts: Mapping[str, bytes]):
    """A resolver over artifacts carried in a bundle, with no filesystem at all.

    This is what lets an independent verifier repeat the binding. Threading a temporary
    directory through verification instead would make offline checking depend on being able
    to write to disk, which is precisely what a portable bundle exists to avoid.
    """

    def resolve(digest: str) -> Mapping:
        raw = artifacts.get(digest)
        if raw is None:
            raise ApprovalError(f"the bundle carries no compilation {digest}")
        return compilation_from_bytes(digest, raw)

    return resolve


def accepted_candidates(compilation: Mapping) -> list[Mapping]:
    """Every candidate this compilation accepted, as the compiler recorded it."""
    outcomes = compilation.get("outcomes")
    if not isinstance(outcomes, list):
        raise ApprovalError("a compilation must carry its outcomes")
    return [
        outcome["control"]
        for outcome in outcomes
        if isinstance(outcome, Mapping)
        and outcome.get("status") == "accepted"
        and isinstance(outcome.get("control"), Mapping)
    ]


def assert_ledger_approves(
    control_id: str, digest: str, *, ledger: Mapping | None = None
) -> None:
    """Refuse a control the committed ledger does not approve, exactly once.

    Without this the declined list is decorative: `holdings-line-items-present` was
    declined on the record and still resolved cleanly to an approved control, because
    resolution only ever consulted the artifact — which of course still contains the
    candidate a human rejected. A decline that cannot refuse anything is a note, not a
    control.
    """
    ledger = load_approval_ledger() if ledger is None else ledger
    pair = (control_id, digest)
    declined = [
        entry
        for entry in ledger[DECLINED_KEY]
        if isinstance(entry, Mapping)
        and (entry.get("control_id"), entry.get("compilation_sha256")) == pair
    ]
    if declined:
        reason = declined[0].get("reason", "no reason recorded")
        raise ApprovalError(
            f"{control_id!r} from compilation {digest} was declined: {reason}"
        )
    approved = [
        entry
        for entry in ledger[APPROVED_KEY]
        if isinstance(entry, Mapping)
        and (entry.get("control_id"), entry.get("compilation_sha256")) == pair
    ]
    if not approved:
        raise ApprovalError(
            f"{control_id!r} from compilation {digest} is not in the approval ledger"
        )
    if len(approved) > 1:
        # Two approvals of one pair make "the approval" undefined, and would let a later
        # entry silently shadow an earlier one.
        raise ApprovalError(
            f"{control_id!r} from compilation {digest} is approved {len(approved)} times"
        )


def approved_control(entry: Mapping, *, resolve=None) -> ControlRecord:
    """Resolve one ledger entry into the approved control, or refuse to.

    The entry names a control and the artifact it was approved from. Everything else about
    the control comes from that artifact, so the ledger cannot restate a control into
    something the compiler never proposed — it can only point at one.
    """
    if not isinstance(entry, Mapping):
        raise ApprovalError("an approval entry must be a mapping")
    digest = entry.get("compilation_sha256")
    control_id = entry.get("control_id")
    if not isinstance(digest, str) or not isinstance(control_id, str):
        raise ApprovalError("an approval entry must name a control and a compilation")
    assert_ledger_approves(control_id, digest)
    return _candidate_as_approved(control_id, digest, resolve)


def _candidate_as_approved(control_id: str, digest: str, resolve) -> ControlRecord:
    """The one accepted candidate under this name, with approval's two fields applied.

    Deliberately free of the ledger. An independent verifier holds the artifacts a bundle
    carries and nothing else — it can check that a control is exactly what a compilation
    accepted, which is the claim the digest makes, but it has no access to the publisher's
    curation record and must not be made to depend on one.
    """
    resolve = from_directory() if resolve is None else resolve
    candidates = [
        candidate
        for candidate in accepted_candidates(resolve(digest))
        if candidate.get("control_id") == control_id
    ]
    if not candidates:
        raise ApprovalError(
            f"compilation {digest} accepted no candidate called {control_id!r}"
        )
    if len(candidates) > 1:
        # Two candidates under one name make "the one that was approved" undefined.
        raise ApprovalError(
            f"compilation {digest} accepted {len(candidates)} candidates called "
            f"{control_id!r}; the approval is ambiguous"
        )
    candidate = candidates[0]
    if candidate.get("compilation_sha256") is not None:
        raise ApprovalError(
            f"the proposal for {control_id!r} carries a compilation digest; a proposal "
            "cannot name the artifact that contains it"
        )
    if candidate.get("approval_state") != "proposed":
        raise ApprovalError(f"the candidate for {control_id!r} is not a proposal")
    return ControlRecord.from_mapping(
        {**candidate, "approval_state": "approved", "compilation_sha256": digest}
    )


def assert_binding(control: ControlRecord, *, resolve=None) -> None:
    """Refuse an approved control that is not exactly what its compilation accepted.

    This is the check that makes the digest mean something. Without it a control could name
    any artifact at all, and the report's provenance would be a well-formed hex string
    pointing at a compilation that never proposed it.
    """
    if control.approval_state == "proposed":
        # Refused outright rather than passed over. Returning here let a proposal through
        # the report boundary, where its null digest then reached `sorted()` beside real
        # ones and failed as a bare TypeError — a provenance defect reported as a crash.
        raise ApprovalError(
            f"{control.control_id!r} is a proposal; only approved controls may be reported"
        )
    if control.approval_state != "approved":
        raise ApprovalError(
            f"{control.control_id!r} has an unrecognised approval state "
            f"{control.approval_state!r}"
        )
    if control.compilation_sha256 is None:
        raise ApprovalError(
            f"approved control {control.control_id!r} names no compilation; nothing "
            "attests that a compiler ever proposed it"
        )
    resolved = _candidate_as_approved(
        control.control_id, control.compilation_sha256, resolve
    )
    # Field by field rather than by content hash. The hash enforces the same invariant, but
    # a mismatch reports only that something differs — and the whole point of this check is
    # to name what an approval changed that it was not allowed to change.
    expected = resolved.to_mapping()
    actual = control.to_mapping()
    edited = sorted(
        name
        for name in expected
        if expected[name] != actual.get(name) and name not in _APPROVAL_FIELDS
    )
    if edited:
        raise ApprovalError(
            f"approved control {control.control_id!r} differs from the candidate "
            f"compilation {control.compilation_sha256} accepted, in: {', '.join(edited)}"
        )


def provenance_digests(controls, *, resolve=None) -> list[str]:
    """The compilation digests an observation report must carry, and only those.

    Derived from the approved controls rather than supplied alongside them. A caller-supplied
    list could name a compilation that produced none of the evaluated controls, which is the
    shape the old report builder accepted.
    """
    digests = []
    for control in controls:
        assert_binding(control, resolve=resolve)
        if control.compilation_sha256 not in digests:
            digests.append(control.compilation_sha256)
    if not digests:
        raise ApprovalError("no approved control names a compilation")
    return sorted(digests)
