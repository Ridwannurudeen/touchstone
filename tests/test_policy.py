"""A policy may only ever ask for less than a human approved, and never for something else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from touchstone.approval import ledger_from_bytes
from touchstone.evaluate import default_ustb_controls
from touchstone.policy import (
    MANIFEST_VERSION,
    POLICIES,
    Policy,
    PolicyError,
    load,
    load_all,
    select,
)

ROOT = Path(__file__).parents[1]
ASSET = "eip155:1:0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e"


@pytest.fixture(scope="module")
def approved():
    ledger = (ROOT / "data" / "compilations" / "APPROVALS.json").read_bytes()
    return default_ustb_controls(ledger_from_bytes(ledger))


def manifest(tmp_path: Path, **changes: object) -> Path:
    document: dict[str, object] = {
        "version": MANIFEST_VERSION,
        "policy_id": "test-policy",
        "policy_version": 1,
        "asset_key": ASSET,
        "title": "Test policy",
        "consumer_question": "Does the thing hold?",
        "controls": ["ustb-nav-daily-freshness"],
    }
    document.update(changes)
    for key in [k for k, v in document.items() if v is None]:
        del document[key]
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{document.get('policy_id', 'x')}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


class TestAPolicyCannotExtendTheApprovedSet:
    def test_an_unapproved_control_is_refused(self, tmp_path: Path, approved) -> None:
        """The one property that makes a policy safe rather than a second decision point."""
        path = manifest(tmp_path, controls=["ustb-nav-daily-freshness", "invented"])
        with pytest.raises(PolicyError, match="has not approved"):
            load(path, approved=approved)

    def test_a_declined_control_is_refused(self, tmp_path: Path, approved) -> None:
        """A human declined these by name; a policy must not reinstate one."""
        ledger = json.loads(
            (ROOT / "data" / "compilations" / "APPROVALS.json").read_text(
                encoding="utf-8"
            )
        )
        declined = ledger["declined"][0]["control_id"]
        path = manifest(tmp_path, controls=[declined])
        with pytest.raises(PolicyError, match="has not approved"):
            load(path, approved=approved)

    def test_a_control_bound_to_another_asset_is_refused(
        self, tmp_path: Path, approved
    ) -> None:
        path = manifest(tmp_path, asset_key="eip155:1:0x" + "ab" * 20)
        with pytest.raises(PolicyError, match="another asset"):
            load(path, approved=approved)

    def test_it_cannot_alter_a_threshold(self, tmp_path: Path, approved) -> None:
        """There is nowhere to put one: a manifest carries ids, not control bodies.

        This is asserted rather than assumed because the whole safety argument rests on a
        policy being a lens over approved decisions. A schema that accepted a control body
        would make it a second approval boundary.
        """
        path = manifest(
            tmp_path,
            controls=[
                {
                    "control_id": "ustb-nav-daily-freshness",
                    "expected_value": {"business_days": 99},
                }
            ],
        )
        with pytest.raises(PolicyError):
            load(path, approved=approved)


class TestManifestStrictness:
    def test_unknown_fields_are_refused(self, tmp_path: Path, approved) -> None:
        path = manifest(tmp_path, sneaky="value")
        with pytest.raises(PolicyError, match="exactly the documented set"):
            load(path, approved=approved)

    def test_missing_fields_are_refused(self, tmp_path: Path, approved) -> None:
        path = manifest(tmp_path, consumer_question=None)
        with pytest.raises(PolicyError, match="exactly the documented set"):
            load(path, approved=approved)

    def test_an_unsupported_manifest_version_is_refused(
        self, tmp_path: Path, approved
    ) -> None:
        path = manifest(tmp_path, version="touchstone.policy-manifest.v99")
        with pytest.raises(PolicyError, match="unsupported manifest version"):
            load(path, approved=approved)

    @pytest.mark.parametrize(
        "bad", ["Has Caps", "trailing-", "under_score", "", "a--b"]
    )
    def test_a_policy_id_that_renders_differently_elsewhere_is_refused(
        self, tmp_path: Path, approved, bad: str
    ) -> None:
        """The id is part of a chain key and part of a URL; ambiguity there is permanent."""
        path = manifest(tmp_path, policy_id=bad)
        with pytest.raises(PolicyError, match="policy_id"):
            load(path, approved=approved)

    @pytest.mark.parametrize("bad", [0, -1, 1.0, "1", True])
    def test_a_non_positive_integer_version_is_refused(
        self, tmp_path: Path, approved, bad: object
    ) -> None:
        path = manifest(tmp_path, policy_version=bad)
        with pytest.raises(PolicyError, match="policy_version"):
            load(path, approved=approved)

    def test_an_empty_control_list_is_refused(self, tmp_path: Path, approved) -> None:
        path = manifest(tmp_path, controls=[])
        with pytest.raises(PolicyError, match="non-empty"):
            load(path, approved=approved)

    def test_a_repeated_control_is_refused(self, tmp_path: Path, approved) -> None:
        path = manifest(
            tmp_path, controls=["ustb-nav-daily-freshness", "ustb-nav-daily-freshness"]
        )
        with pytest.raises(PolicyError, match="repeat"):
            load(path, approved=approved)

    def test_malformed_json_is_refused(self, tmp_path: Path, approved) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PolicyError, match="strict JSON"):
            load(path, approved=approved)


class TestIdentityAndDigest:
    def test_control_order_does_not_change_the_policy(
        self, tmp_path: Path, approved
    ) -> None:
        """Two orderings are one selection, so they cannot be presented as two policies."""
        forward = load(
            manifest(
                tmp_path / "a",
                controls=["ustb-nav-daily-freshness", "ustb-yield-one-day-present"],
            ),
            approved=approved,
        )
        reverse = load(
            manifest(
                tmp_path / "b",
                controls=["ustb-yield-one-day-present", "ustb-nav-daily-freshness"],
            ),
            approved=approved,
        )
        assert forward.control_ids == reverse.control_ids

    def test_the_digest_follows_the_bytes(self, tmp_path: Path, approved) -> None:
        """A policy edited in place must not keep the digest a signed report committed to."""
        first = load(manifest(tmp_path / "a", title="One"), approved=approved)
        second = load(manifest(tmp_path / "b", title="Two"), approved=approved)
        assert first.digest != second.digest

    def test_the_key_extends_the_asset_identifier(
        self, tmp_path: Path, approved
    ) -> None:
        policy = load(manifest(tmp_path), approved=approved)
        assert policy.key.startswith(ASSET)
        assert policy.key == f"{ASSET}#policy:test-policy:1"

    def test_two_versions_are_two_keys(self, tmp_path: Path, approved) -> None:
        one = load(manifest(tmp_path / "a", policy_version=1), approved=approved)
        two = load(manifest(tmp_path / "b", policy_version=2), approved=approved)
        assert one.key != two.key


class TestLoadAll:
    def test_a_duplicated_version_is_refused(self, tmp_path: Path, approved) -> None:
        """Publishing one version twice would give two histories for one identifier."""
        manifest(tmp_path, policy_id="same")
        second = tmp_path / "also.json"
        second.write_text(
            (tmp_path / "same.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        with pytest.raises(PolicyError, match="declare"):
            load_all(tmp_path, approved=approved)

    def test_an_absent_directory_is_no_policies(self, tmp_path: Path, approved) -> None:
        assert load_all(tmp_path / "nothing", approved=approved) == ()


class TestSelect:
    def test_it_returns_only_the_policy_controls(self, approved) -> None:
        policies = load_all(approved=approved)
        freshness = next(p for p in policies if p.policy_id == "disclosure-freshness")
        chosen = select(freshness, approved)
        assert {c.control_id for c in chosen} == set(freshness.control_ids)
        assert len(chosen) < len(approved)

    def test_it_refuses_rather_than_silently_narrowing(self, approved) -> None:
        """Evaluating fewer controls than the policy names would report an unasked question."""
        policy = Policy(
            policy_id="p",
            version=1,
            asset_key=ASSET,
            title="t",
            consumer_question="q",
            control_ids=("ustb-nav-daily-freshness", "absent-control"),
            digest="0" * 64,
        )
        with pytest.raises(PolicyError, match="absent from the resolved set"):
            select(policy, approved)


class TestTheShippedPolicies:
    def test_both_ship_and_resolve(self, approved) -> None:
        policies = load_all(approved=approved)
        assert {p.policy_id for p in policies} == {
            "disclosure-freshness",
            "nav-settlement",
        }

    def test_every_shipped_policy_is_a_strict_subset(self, approved) -> None:
        approved_ids = {c.control_id for c in approved}
        for policy in load_all(approved=approved):
            assert set(policy.control_ids) < approved_ids, (
                f"{policy.policy_id} is not a strict subset; a policy asking for everything "
                "is the existing asset-wide verdict under another name"
            )

    def test_the_shipped_manifests_live_where_the_module_looks(self) -> None:
        assert POLICIES.is_dir()
        assert sorted(p.name for p in POLICIES.glob("*.json")) == [
            "disclosure-freshness-v1.json",
            "nav-settlement-v1.json",
        ]
