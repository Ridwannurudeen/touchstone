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
import re

from eth_account import Account
from eth_account.messages import encode_typed_data

from touchstone.controls import ControlRecord


ROOT = Path(__file__).parents[1]
COMPILATIONS = ROOT / "data" / "compilations"
LEDGER = ROOT / "data" / "compilations" / "APPROVALS.json"
# v2 requires a signed approval on every entry, recovered to one approver. v1 verifies a
# signature wherever an entry carries one and demands nothing where it does not — which is
# the shape both external audits named the weakest link: an unauthenticated `approval_state`
# is a claim, not a control. Published bundles carry v1 ledgers and verify forever.
LEDGER_VERSION = "touchstone.approval-ledger.v2"
LEDGER_VERSION_V1 = "touchstone.approval-ledger.v1"
APPROVED_KEY = "approved"
DECLINED_KEY = "declined"
LEGACY_APPROVAL_SIGNATURE_VERSION = 1
APPROVAL_SIGNATURE_VERSION = 2
APPROVAL_SCOPE_GLOBAL = "global"
APPROVAL_SCOPE_POLICY = "policy"
APPROVAL_DOMAIN = {"name": "Touchstone Approval", "version": "2"}
_LEGACY_APPROVAL_DOMAIN = {"name": "Touchstone Approval", "version": "1"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_POLICY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SIGNATURE = re.compile(r"[0-9a-f]{130}")
_LEGACY_SIGNED_APPROVAL_FIELDS = frozenset(
    {
        "version",
        "control_digest",
        "compilation_digest",
        "decision",
        "reason_code",
        "timestamp",
        "approver",
        "signature",
    }
)
_SIGNED_APPROVAL_FIELDS = _LEGACY_SIGNED_APPROVAL_FIELDS | {"scope", "policy_id"}

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


def approval_typed_data(
    *,
    control_digest: str,
    compilation_digest: str,
    decision: str,
    reason_code: str,
    timestamp: int,
    version: int = APPROVAL_SIGNATURE_VERSION,
    scope: str = APPROVAL_SCOPE_GLOBAL,
    policy_id: str = "",
) -> dict[str, object]:
    """Return the exact EIP-712 payload signed for one approval decision."""
    _validate_digest("control_digest", control_digest)
    _validate_digest("compilation_digest", compilation_digest)
    if decision not in {APPROVED_KEY, DECLINED_KEY}:
        raise ApprovalError("approval decision must be approved or declined")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise ApprovalError("approval reason_code must be a non-empty string")
    if type(timestamp) is not int or timestamp <= 0:
        raise ApprovalError("approval timestamp must be a positive integer")
    if version == LEGACY_APPROVAL_SIGNATURE_VERSION:
        domain = _LEGACY_APPROVAL_DOMAIN
    elif version == APPROVAL_SIGNATURE_VERSION:
        _validate_scope(scope, policy_id)
        domain = APPROVAL_DOMAIN
    else:
        raise ApprovalError("signed approval version is not supported")
    approval_fields = [
        {"name": "control_digest", "type": "bytes32"},
        {"name": "compilation_digest", "type": "bytes32"},
        {"name": "decision", "type": "string"},
        {"name": "reason_code", "type": "string"},
        {"name": "timestamp", "type": "uint256"},
    ]
    message = {
        "control_digest": "0x" + control_digest,
        "compilation_digest": "0x" + compilation_digest,
        "decision": decision,
        "reason_code": reason_code,
        "timestamp": timestamp,
    }
    if version == APPROVAL_SIGNATURE_VERSION:
        approval_fields.extend(
            [
                {"name": "scope", "type": "string"},
                {"name": "policyId", "type": "string"},
            ]
        )
        message.update({"scope": scope, "policyId": policy_id})
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
            ],
            "Approval": approval_fields,
        },
        "primaryType": "Approval",
        "domain": dict(domain),
        "message": message,
    }


def sign_approval(
    private_key: str | bytes,
    *,
    control_digest: str,
    compilation_digest: str,
    decision: str,
    reason_code: str,
    timestamp: int,
    scope: str = APPROVAL_SCOPE_GLOBAL,
    policy_id: str = "",
) -> dict[str, object]:
    """Create the signed EIP-712 approval artifact from explicit key material."""
    typed_data = approval_typed_data(
        control_digest=control_digest,
        compilation_digest=compilation_digest,
        decision=decision,
        reason_code=reason_code,
        timestamp=timestamp,
        scope=scope,
        policy_id=policy_id,
    )
    try:
        account = Account.from_key(private_key)
        signature = Account.sign_message(
            encode_typed_data(full_message=typed_data), private_key
        ).signature
    except (TypeError, ValueError) as error:
        raise ApprovalError(f"approval signing failed: {error}") from error
    return {
        "version": APPROVAL_SIGNATURE_VERSION,
        "control_digest": control_digest,
        "compilation_digest": compilation_digest,
        "decision": decision,
        "reason_code": reason_code,
        "timestamp": timestamp,
        "scope": scope,
        "policy_id": policy_id,
        "approver": account.address,
        "signature": signature.hex(),
    }


def verify_signed_approval(
    value: object,
    *,
    expected_decision: str | None = None,
    expected_scope: str | None = None,
    expected_policy_id: str | None = None,
) -> str:
    """Verify one signed approval and return its recovered approver address."""
    if not isinstance(value, Mapping):
        raise ApprovalError("signed approval must be a mapping")
    supplied = set(value)
    if "version" not in supplied:
        raise ApprovalError("signed approval is missing field(s): version")
    version = value["version"]
    if version == LEGACY_APPROVAL_SIGNATURE_VERSION:
        required_fields = _LEGACY_SIGNED_APPROVAL_FIELDS
    elif version == APPROVAL_SIGNATURE_VERSION:
        required_fields = _SIGNED_APPROVAL_FIELDS
    else:
        raise ApprovalError("signed approval version is not supported")
    unknown = supplied - required_fields
    missing = required_fields - supplied
    if unknown:
        raise ApprovalError(
            "signed approval has unknown field(s): " + ", ".join(sorted(unknown))
        )
    if missing:
        raise ApprovalError(
            "signed approval is missing field(s): " + ", ".join(sorted(missing))
        )
    decision = value["decision"]
    if decision not in {APPROVED_KEY, DECLINED_KEY}:
        raise ApprovalError("signed approval decision is not supported")
    if expected_decision is not None and decision != expected_decision:
        raise ApprovalError("signed approval decision does not match its ledger list")
    _validate_digest("control_digest", value["control_digest"])
    _validate_digest("compilation_digest", value["compilation_digest"])
    if not isinstance(value["reason_code"], str) or not value["reason_code"].strip():
        raise ApprovalError("signed approval reason_code must be a non-empty string")
    timestamp = value["timestamp"]
    if type(timestamp) is not int or timestamp <= 0:
        raise ApprovalError("signed approval timestamp must be a positive integer")
    scope = APPROVAL_SCOPE_GLOBAL
    policy_id = ""
    if version == APPROVAL_SIGNATURE_VERSION:
        scope = value["scope"]
        policy_id = value["policy_id"]
        _validate_scope(scope, policy_id)
    approver = value["approver"]
    if not isinstance(approver, str) or re.fullmatch(r"0x[0-9a-fA-F]{40}", approver) is None:
        raise ApprovalError("signed approval approver must be an address")
    try:
        signature = bytes.fromhex(value["signature"])
    except (TypeError, ValueError) as error:
        raise ApprovalError("signed approval signature must be lowercase hexadecimal") from error
    if (
        not isinstance(value["signature"], str)
        or _SIGNATURE.fullmatch(value["signature"]) is None
    ):
        raise ApprovalError("signed approval signature must be lowercase hexadecimal")
    try:
        recovered = Account.recover_message(
            encode_typed_data(
                full_message=approval_typed_data(
                    control_digest=value["control_digest"],
                    compilation_digest=value["compilation_digest"],
                    decision=decision,
                    reason_code=value["reason_code"],
                    timestamp=timestamp,
                    version=version,
                    scope=scope,
                    policy_id=policy_id,
                )
            ),
            signature=signature,
        )
    except (TypeError, ValueError) as error:
        raise ApprovalError("signed approval signature is invalid") from error
    if recovered.lower() != approver.lower():
        raise ApprovalError("signed approval approver does not match its signature")
    if expected_scope is not None:
        if expected_scope not in {APPROVAL_SCOPE_GLOBAL, APPROVAL_SCOPE_POLICY}:
            raise ApprovalError("expected approval scope is not supported")
        if version == LEGACY_APPROVAL_SIGNATURE_VERSION:
            if expected_scope != APPROVAL_SCOPE_GLOBAL:
                raise ApprovalError("legacy signed approval has no signed policy scope")
        elif scope != expected_scope:
            raise ApprovalError("signed approval scope does not match its use")
    if expected_policy_id is not None:
        if _POLICY_ID.fullmatch(expected_policy_id) is None:
            raise ApprovalError("expected policy_id is not valid")
        if version == LEGACY_APPROVAL_SIGNATURE_VERSION:
            raise ApprovalError("legacy signed approval has no signed policy_id")
        if policy_id != expected_policy_id:
            raise ApprovalError("signed approval policy_id does not match its use")
    return recovered


def _validate_digest(field: str, value: object) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ApprovalError(f"{field} must be a lowercase SHA-256 digest")


def _validate_scope(scope: object, policy_id: object) -> None:
    if scope not in {APPROVAL_SCOPE_GLOBAL, APPROVAL_SCOPE_POLICY}:
        raise ApprovalError("approval scope must be global or policy")
    if not isinstance(policy_id, str):
        raise ApprovalError("approval policy_id must be a string")
    if scope == APPROVAL_SCOPE_GLOBAL and policy_id:
        raise ApprovalError("global approval must not name a policy_id")
    if scope == APPROVAL_SCOPE_POLICY and _POLICY_ID.fullmatch(policy_id) is None:
        raise ApprovalError(
            "policy-scoped approval must name a lowercase hyphenated policy_id"
        )


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
        _assert_signed_control(matches[0], control)


def ledger_from_bytes(raw: bytes) -> Mapping[str, list]:
    """Parse and validate a ledger carried in a bundle, with no filesystem involved."""
    try:
        ledger = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError(f"the approval ledger is not readable JSON: {error}") from error
    return _validated_ledger(ledger)


def _validated_ledger(ledger: object) -> Mapping[str, list]:
    """One shape check, shared by the on-disk and in-bundle readers."""
    if not isinstance(ledger, Mapping) or ledger.get("version") not in (
        LEDGER_VERSION,
        LEDGER_VERSION_V1,
    ):
        raise ApprovalError("the approval ledger is not a supported version")
    strict = ledger.get("version") == LEDGER_VERSION
    for key in (APPROVED_KEY, DECLINED_KEY):
        if not isinstance(ledger.get(key), list):
            raise ApprovalError(f"the approval ledger has no {key} list")
        for entry in ledger[key]:
            if not isinstance(entry, Mapping) or "approval" not in entry:
                # One approver across a ledger is today's operational fact, but it is not
                # a rule of the format: making it one would force re-signing history the
                # day the approver key rotates, and manufactured history is the one thing
                # this project must never produce. What the format does demand is that
                # every version-2 decision carries a signature at all.
                if strict:
                    named = entry.get("control_id") if isinstance(entry, Mapping) else None
                    raise ApprovalError(
                        "a version-2 approval ledger requires a signed approval on "
                        f"every entry, and {named or 'an entry'} in {key} has none"
                    )
                continue
            _verify_ledger_entry_signature(entry, key)
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
    validated = _validated_ledger(ledger)
    # A version-2 ledger binds each signed decision to the proposal it decides, and this
    # is the load path where the named compilations are always on disk to check against.
    # `ledger_from_bytes` cannot promise that — a bundle carries the artifacts its report
    # needs, not the publisher's whole compilation store — so its reader binds whatever
    # the bundle holds and says so, in `verify._verify_approval_ledger`.
    if validated.get("version") == LEDGER_VERSION:
        resolve = from_directory()
        for key in (APPROVED_KEY, DECLINED_KEY):
            for entry in validated[key]:
                assert_entry_proposal(entry, resolve=resolve)
    return validated


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
    _verify_ledger_entry_signature(approved[0], APPROVED_KEY)


def _verify_ledger_entry_signature(entry: Mapping, decision: str) -> str:
    """Verify a signed entry and ensure its outer ledger fields cannot drift."""
    signed = entry.get("approval")
    if signed is None:
        return ""
    approver = verify_signed_approval(
        signed,
        expected_decision=decision,
        expected_scope=APPROVAL_SCOPE_GLOBAL,
    )
    if entry.get("compilation_sha256") != signed.get("compilation_digest"):
        raise ApprovalError("signed approval compilation digest does not match its ledger entry")
    return approver


def _assert_signed_control(entry: Mapping, control: ControlRecord) -> None:
    """Bind a signed approval to the exact compiler proposal it approved."""
    signed = entry.get("approval")
    if signed is None:
        return
    _verify_ledger_entry_signature(entry, APPROVED_KEY)
    proposal_mapping = control.to_mapping()
    proposal_mapping.update({"approval_state": "proposed", "compilation_sha256": None})
    proposal = ControlRecord.from_mapping(proposal_mapping)
    if signed.get("control_digest") != proposal.content_hash:
        raise ApprovalError(
            f"signed approval for {control.control_id!r} does not match the compiler proposal"
        )


def assert_entry_proposal(entry: Mapping, *, resolve=None) -> None:
    """Bind one signed decision to the exact proposal its compilation records.

    `_assert_signed_control` does this for a control on its way into a report, which
    covers approved entries and nothing else. A signed decline needed the same binding:
    its signature covers a control digest and a compilation digest, but the outer
    `control_id` — the field a decline actually refuses by — was tied to neither, so one
    candidate's signed refusal could sit on another candidate's entry and refuse the
    wrong control.
    """
    signed = entry.get("approval")
    if signed is None:
        return
    control_id = entry.get("control_id")
    digest = entry.get("compilation_sha256")
    if not isinstance(digest, str) or not isinstance(control_id, str):
        raise ApprovalError("an approval entry must name a control and a compilation")
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
    proposals = {
        ControlRecord.from_mapping(candidate).content_hash for candidate in candidates
    }
    if signed.get("control_digest") not in proposals:
        raise ApprovalError(
            f"the signed decision on {control_id!r} does not match the compiler proposal"
        )


def approved_control(entry: Mapping, *, resolve=None, ledger: Mapping | None = None) -> ControlRecord:
    """Resolve one ledger entry into the approved control, or refuse to.

    The entry names a control and the artifact it was approved from. Everything else about
    the control comes from that artifact, so the ledger cannot restate a control into
    something the compiler never proposed — it can only point at one.

    ``ledger`` is the snapshot the entry came from. Without it this re-read the ledger file
    once **per control**, so resolving eight controls meant nine reads of one file in a
    single slot — the caller's, plus one hidden inside each resolution. Threading it through
    is what makes "read the ledger once" true rather than nearly true.
    """
    if not isinstance(entry, Mapping):
        raise ApprovalError("an approval entry must be a mapping")
    digest = entry.get("compilation_sha256")
    control_id = entry.get("control_id")
    if not isinstance(digest, str) or not isinstance(control_id, str):
        raise ApprovalError("an approval entry must name a control and a compilation")
    assert_ledger_approves(control_id, digest, ledger=ledger)
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
