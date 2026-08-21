"""Fail when canonical project state contradicts deployment or public copy."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

STATE_VERSION = "touchstone.project-state.v1"
# Every phrase here was found on a live public surface AFTER the fact it denied had
# happened; each entry is a defect this gate failed to catch once. The list grows in one
# direction only.
STALE_PHRASES = (
    "the dossier was unbuilt",
    "the gate never deployed",
    "Nothing has ever reached",
    "No positive answer is asserted",
    "not claim that v2 is deployed",
    "Every one of them reports `UNVERIFIABLE`",
    "no run has yet had one that qualified",
    "3 could not be",
    "what the mainnet gate answers today",
    "the four v2 attestations",
)
POLICY_STATES = ("CONFIRMED", "STALE", "INCONSISTENT", "UNVERIFIABLE")


class PublicTruthError(RuntimeError):
    """The public record disagrees with canonical project state."""


class PolicyPanelParser(HTMLParser):
    """Collect machine-readable policy panels and their visible text."""

    def __init__(self) -> None:
        super().__init__()
        self.panels: list[tuple[dict[str, str], str]] = []
        self._attributes: dict[str, str] | None = None
        self._depth = 0
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if self._attributes is not None:
            self._depth += 1
        elif tag == "div" and "data-policy-panel" in attributes:
            self._attributes = attributes
            self._depth = 1
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._attributes is None:
            return
        self._depth -= 1
        if self._depth == 0:
            self.panels.append((self._attributes, " ".join(self._text)))
            self._attributes = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._attributes is not None:
            self._text.append(data)


def assert_chain_fact_surfaces(
    facts: dict[str, object], stats: dict[str, object], readme: str
) -> None:
    counts = facts.get("counts")
    if not isinstance(counts, dict):
        raise PublicTruthError("chain facts have no counts object")
    published_text = counts.get("reports_published")
    confirmed_text = counts.get("confirmed_reports")
    if (
        not isinstance(published_text, str)
        or not published_text.isdigit()
        or not isinstance(confirmed_text, str)
        or not confirmed_text.isdigit()
    ):
        raise PublicTruthError("chain report counts must be integers")
    published = int(published_text)
    confirmed = int(confirmed_text)

    if stats.get("reports_published") != published:
        raise PublicTruthError("public stats report count disagrees with chain facts")
    if stats.get("confirmed_reports") != confirmed:
        raise PublicTruthError(
            "public stats confirmed-report count disagrees with chain facts"
        )
    reports = stats.get("reports")
    if not isinstance(reports, list) or len(reports) != published:
        raise PublicTruthError("public stats report list disagrees with its report count")
    if sum(
        isinstance(report, dict) and report.get("state") == "CONFIRMED"
        for report in reports
    ) != confirmed:
        raise PublicTruthError(
            "public stats report states disagree with its confirmed-report count"
        )

    headline = re.search(
        r"^Status:.*?(\d+) published reports, (\d+) `CONFIRMED`",
        readme,
        flags=re.MULTILINE,
    )
    if headline is None or tuple(map(int, headline.groups())) != (published, confirmed):
        raise PublicTruthError("README report count disagrees with chain facts")


def assert_state(state: dict[str, object], public_paths: tuple[Path, ...]) -> None:
    if state.get("version") != STATE_VERSION:
        raise PublicTruthError("unsupported project-state version")
    deployments = state.get("deployments")
    if not isinstance(deployments, list):
        raise PublicTruthError("project state has no deployment list")
    for deployment in deployments:
        if not isinstance(deployment, dict):
            raise PublicTruthError("deployment records must be objects")
        address = deployment.get("registry_address")
        if (
            isinstance(address, str)
            and address.lower().startswith("0x")
            and set(address[2:]) == {"0"}
        ):
            raise PublicTruthError(
                f"{deployment.get('source')} describes a zero address as deployed"
            )
        if (
            address
            and address != "not_deployed"
            and deployment.get("deployment_state")
            not in {
                "active",
                "superseded",
            }
        ):
            raise PublicTruthError(
                f"{deployment.get('source')} has an address but no deployed state"
            )
        if (
            not isinstance(deployment.get("network"), str)
            or type(deployment.get("chain_id")) is not int
        ):
            raise PublicTruthError(
                f"{deployment.get('source')} names a network without a chain id"
            )
    reports = state.get("reports")
    bundles = state.get("bundles")
    if not isinstance(reports, dict) or not isinstance(bundles, list):
        raise PublicTruthError("project state has incomplete report facts")
    if reports.get("artifact_count") != len(bundles):
        raise PublicTruthError("report count disagrees with bundled artifacts")
    policies = state.get("policies")
    if not isinstance(policies, list):
        raise PublicTruthError("project state has no policy list")
    declared_policy_keys: set[tuple[str, int]] = set()
    for policy in policies:
        if not isinstance(policy, dict):
            raise PublicTruthError("policy records must be objects")
        policy_id, policy_version = (
            policy.get("policy_id"),
            policy.get("policy_version"),
        )
        if not isinstance(policy_id, str) or type(policy_version) is not int:
            raise PublicTruthError("policy records must name an id and integer version")
        declared_policy_keys.add((policy_id, policy_version))
    retained_policy_bundles = []
    confirmed_policy_keys: set[tuple[str, int]] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict):
            raise PublicTruthError("bundle records must be objects")
        policy = bundle.get("policy")
        if policy is None:
            continue
        if not isinstance(policy, dict):
            raise PublicTruthError("bundle policy records must be objects or null")
        key = (policy.get("policy_id"), policy.get("policy_version"))
        if key not in declared_policy_keys:
            raise PublicTruthError(f"bundle names an undeclared policy {key!r}")
        retained_policy_bundles.append(bundle)
        if bundle.get("state") == "CONFIRMED":
            confirmed_policy_keys.add(key)
    if reports.get("retained_verified_policy_bundle_count") != len(
        retained_policy_bundles
    ):
        raise PublicTruthError(
            "retained verified policy-bundle count disagrees with bundled artifacts"
        )
    if reports.get("confirmed_policy_bundle_count") != sum(
        bundle.get("state") == "CONFIRMED" for bundle in retained_policy_bundles
    ):
        raise PublicTruthError(
            "confirmed policy-bundle count disagrees with bundled artifacts"
        )
    approval = state.get("approval")
    if not isinstance(approval, dict) or approval.get("approved_count") != len(
        approval.get("approved_control_ids", [])
    ):
        raise PublicTruthError("approved control count disagrees with the ledger")
    for path in public_paths:
        if path.is_dir():
            files = sorted(path.rglob("*.html"))
        else:
            files = [path]
        for public_file in files:
            text = public_file.read_text(encoding="utf-8").lower()
            for phrase in STALE_PHRASES:
                # The document is lowercased above, so the phrase must be lowered here
                # too — comparing it as recorded left every phrase containing an
                # uppercase letter unable to match anything, which was five of the
                # eight. Found by an external audit on 2026-08-20.
                if phrase.lower() in text:
                    raise PublicTruthError(
                        f"stale public phrase {phrase!r} remains in {public_file}"
                    )
            if public_file.suffix.lower() == ".html":
                parser = PolicyPanelParser()
                parser.feed(text)
                for attributes, visible_text in parser.panels:
                    policy_id = attributes.get("data-policy-id")
                    version_text = attributes.get("data-policy-version")
                    declared_state = attributes.get("data-policy-state", "").upper()
                    if (
                        not policy_id
                        or not version_text
                        or not version_text.isdigit()
                        or declared_state not in POLICY_STATES
                    ):
                        raise PublicTruthError(
                            f"policy panel in {public_file} lacks canonical policy metadata"
                        )
                    key = (policy_id, int(version_text))
                    if key not in declared_policy_keys:
                        raise PublicTruthError(
                            f"policy panel in {public_file} names undeclared policy {key!r}"
                        )
                    visible_states = {
                        state
                        for state in POLICY_STATES
                        if re.search(
                            rf"(?<![A-Z]){state}(?![A-Z])", visible_text.upper()
                        )
                    }
                    if visible_states != {declared_state}:
                        raise PublicTruthError(
                            f"policy panel in {public_file} visibly claims "
                            f"{sorted(visible_states)!r}, not {declared_state!r}"
                        )
                    if (
                        declared_state == "CONFIRMED"
                        and key not in confirmed_policy_keys
                    ):
                        raise PublicTruthError(
                            f"policy panel in {public_file} claims CONFIRMED for {key!r} "
                            "without a retained verified policy bundle"
                        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("paths", nargs="*", type=Path)
    arguments = parser.parse_args(argv)
    paths = tuple(
        (arguments.root / path if not path.is_absolute() else path)
        for path in (
            arguments.paths
            # OPERATIONS is rendered to the public site, so a stale header there is a
            # public claim like any other — an external audit found it contradicting
            # the live services while this scan looked only at README and site2. The SDK
            # README joined the list the same way: it told public-repo readers that v2
            # was not deployed while the SDK's own address table carried both v2
            # deployments, and this gate never looked at it.
            or (
                Path("README.md"),
                Path("docs/OPERATIONS.md"),
                # LIMITATIONS and the submission draft joined on 2026-08-20: both are
                # public claims the moment the repo is public — the submission draft is
                # literally the text judges receive — and neither was scanned, so a row
                # they froze could contradict the site without anything failing.
                Path("docs/LIMITATIONS.md"),
                Path("docs/SUBMISSION-DRAFT.md"),
                Path("sdk/README.md"),
                Path("site2"),
            )
        )
    )
    try:
        state = json.loads(arguments.state.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise PublicTruthError("project state must be an object")
        facts = json.loads(
            (arguments.root / "site2" / "_data" / "facts.json").read_text(
                encoding="utf-8"
            )
        )
        stats = json.loads(
            (arguments.root / "site2" / "data" / "stats.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(facts, dict) or not isinstance(stats, dict):
            raise PublicTruthError("public fact files must be JSON objects")
        assert_chain_fact_surfaces(
            facts,
            stats,
            (arguments.root / "README.md").read_text(encoding="utf-8"),
        )
        assert_state(state, paths)
    except (OSError, json.JSONDecodeError, PublicTruthError) as error:
        print(f"PUBLIC TRUTH FAIL: {error}", file=sys.stderr)
        return 1
    print("public truth is consistent with project state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
