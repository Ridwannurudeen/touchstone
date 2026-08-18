"""A consumer policy: the subset of approved controls one consumer actually requires.

Until now an asset had one state. That was honest and it was also useless to a consumer,
because it answered a question nobody asked. A listing monitor wants to know whether the
issuer is still publishing current disclosures. A collateral desk wants to know whether the
NAV it would lend against has settled. Those are different questions about the same evidence,
and collapsing them into one verdict means the strictest requirement silently governs every
consumer — so the whole asset reads `UNVERIFIABLE` because one value control abstained, and a
listing monitor is told nothing it can act on.

A policy names a subset of already-approved controls and nothing else:

* it **cannot** introduce a control the approval ledger has not approved;
* it **cannot** alter a threshold, an operator or an expected value;
* it **cannot** be edited in place — a changed policy is a new version with a new key.

So a policy can only ever ask for *less* than the full approved set, never for something
different. That asymmetry is the safety property: a policy is a lens over decisions a human
already made, not a second place where decisions get made.

## Why the manifest is frozen before evaluation, and digest-committed

The obvious failure is choosing the subset after seeing which controls passed. That produces a
green result on demand and is indistinguishable, in the artifact, from a policy that was
declared in advance — which would make every policy state worthless.

The defence is ordering plus commitment: manifests are files with digests, the digest is
committed into the signed report, and the report is signed over both. A policy invented to
match a result would have to be back-dated into a file whose digest is already inside a signed
report published to an append-only registry.

## Why a policy gets its own registry key

`TouchstoneRegistry` is keyed by an opaque `bytes32` and enforces sequence, epoch uniqueness,
corrections and publisher lineage *per key*. Nothing in it requires that key to denote an
asset. A policy therefore publishes under its own key and inherits every one of those
properties from a contract already deployed and already exercised, with no migration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from touchstone.controls import ControlRecord

ROOT = Path(__file__).parents[1]
POLICIES = ROOT / "data" / "policies"
MANIFEST_VERSION = "touchstone.policy-manifest.v1"

# A policy id is part of a chain key and part of a public identifier, so it is deliberately
# narrow: no spaces, no case, nothing that renders differently in a URL than in a log.
_POLICY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PolicyError(RuntimeError):
    """A policy manifest does not describe a policy this project may evaluate."""


@dataclass(frozen=True, slots=True)
class Policy:
    """One versioned consumer policy, resolved against an approval ledger."""

    policy_id: str
    version: int
    asset_key: str
    title: str
    consumer_question: str
    control_ids: tuple[str, ...]
    digest: str

    @property
    def key(self) -> str:
        """The identifier this policy publishes under.

        Deliberately an extension of the asset identifier rather than an opaque hash: a
        reader seeing this string knows which asset it concerns without a lookup table, and
        the existing asset form remains a valid prefix of it.
        """
        return f"{self.asset_key}#policy:{self.policy_id}:{self.version}"


def manifest_bytes(path: str | Path) -> bytes:
    """A manifest's exact bytes, for hashing and for carrying in a bundle."""
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise PolicyError(f"the policy manifest cannot be read: {error}") from error


def digest_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: str | Path, *, approved: Iterable[ControlRecord]) -> Policy:
    """Read one manifest and prove it selects only controls a human approved.

    The approval ledger is required rather than optional. A policy validated without it would
    be a list of strings that looks like a policy, and the one property that makes a policy
    safe — that it cannot introduce a control — would rest on nobody checking.
    """
    raw = manifest_bytes(path)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"{Path(path).name} is not strict JSON: {error}") from error
    if not isinstance(document, Mapping):
        raise PolicyError(f"{Path(path).name} must be a JSON object")

    expected = {
        "version",
        "policy_id",
        "policy_version",
        "asset_key",
        "title",
        "consumer_question",
        "controls",
    }
    if set(document) != expected:
        missing = sorted(expected - set(document))
        unknown = sorted(set(document) - expected)
        raise PolicyError(
            f"{Path(path).name} fields must be exactly the documented set: "
            f"missing={missing}, unknown={unknown}"
        )
    if document["version"] != MANIFEST_VERSION:
        raise PolicyError(f"{Path(path).name} declares an unsupported manifest version")

    policy_id = document["policy_id"]
    if not isinstance(policy_id, str) or _POLICY_ID.fullmatch(policy_id) is None:
        raise PolicyError("policy_id must be lowercase words joined by single hyphens")

    policy_version = document["policy_version"]
    if type(policy_version) is not int or policy_version < 1:
        raise PolicyError("policy_version must be a positive integer")

    for field in ("asset_key", "title", "consumer_question"):
        if not isinstance(document[field], str) or not document[field].strip():
            raise PolicyError(f"{field} must be non-empty text")

    control_ids = document["controls"]
    if not isinstance(control_ids, list) or not control_ids:
        raise PolicyError("controls must be a non-empty list of approved control ids")
    # Ids, never control bodies. Checked before the duplicate test because an unhashable
    # entry crashed it with a TypeError instead of refusing the manifest with a reason —
    # and a manifest carrying a control body is precisely the shape that would make a
    # policy a second approval boundary rather than a lens over the first.
    bad = [item for item in control_ids if not isinstance(item, str) or not item.strip()]
    if bad:
        raise PolicyError(
            "controls must be approved control ids as text; a manifest carrying a control "
            f"body would be defining a control, not selecting one: {bad[:2]}"
        )
    if len(set(control_ids)) != len(control_ids):
        raise PolicyError("controls must not repeat a control id")

    approved_by_id = {control.control_id: control for control in approved}
    unknown = [name for name in control_ids if name not in approved_by_id]
    if unknown:
        raise PolicyError(
            f"{Path(path).name} selects controls the approval ledger has not approved: "
            f"{sorted(unknown)}. A policy narrows an approved set; it cannot extend one."
        )

    wrong_asset = [
        name
        for name in control_ids
        if approved_by_id[name].asset_key != document["asset_key"]
    ]
    if wrong_asset:
        raise PolicyError(
            f"{Path(path).name} names {document['asset_key']} but selects controls bound to "
            f"another asset: {sorted(wrong_asset)}"
        )

    return Policy(
        policy_id=policy_id,
        version=policy_version,
        asset_key=document["asset_key"],
        title=document["title"],
        consumer_question=document["consumer_question"],
        # Sorted, so two manifests listing the same controls in different orders resolve to
        # the same selection and cannot be presented as two different policies.
        control_ids=tuple(sorted(control_ids)),
        digest=digest_of(raw),
    )


def load_all(
    directory: str | Path = POLICIES, *, approved: Iterable[ControlRecord]
) -> tuple[Policy, ...]:
    """Every manifest in a directory, in a stable order, each proven against the ledger."""
    approved = tuple(approved)
    root = Path(directory)
    if not root.is_dir():
        return ()
    policies = tuple(
        load(path, approved=approved) for path in sorted(root.glob("*.json"))
    )
    seen: dict[tuple[str, int], Path] = {}
    for policy, path in zip(policies, sorted(root.glob("*.json"))):
        key = (policy.policy_id, policy.version)
        if key in seen:
            raise PolicyError(
                f"two manifests declare {policy.policy_id} v{policy.version}: "
                f"{seen[key].name} and {path.name}. A version is published once."
            )
        seen[key] = path
    return policies


def select(
    policy: Policy, controls: Sequence[ControlRecord]
) -> tuple[ControlRecord, ...]:
    """The approved controls this policy asks about, in the control set's own order.

    Refuses rather than silently returning fewer, because a policy that quietly evaluated a
    subset of its own subset would report a state derived from requirements it never checked.
    """
    by_id = {control.control_id: control for control in controls}
    missing = [name for name in policy.control_ids if name not in by_id]
    if missing:
        raise PolicyError(
            f"policy {policy.policy_id} v{policy.version} requires controls absent from the "
            f"resolved set: {sorted(missing)}"
        )
    return tuple(by_id[name] for name in policy.control_ids)
