"""Kill the process mid-publication and prove the next one finishes it exactly once.

These tests spawn real subprocesses and kill them with ``os._exit``, which runs no
handlers, flushes nothing, and unwinds nothing — the closest thing to a power cut a test
can arrange. Anything the restart relies on therefore has to already be on disk.

Two crashes matter, and they must come out differently:

- **Before the broadcast.** The journal holds signed bytes the chain never saw. The restart
  must send those exact bytes rather than sign new ones, so one report yields one
  transaction.
- **After the broadcast.** The publication settled but the operation was never cleared. The
  restart must recognise it as already done rather than publish a second time.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from eth_account import Account
import pytest
from web3 import Web3

from touchstone.signing import Ed25519Signer


CHILD = Path(__file__).parent / "service_child.py"
PUBLISHER_SECRET = "a1" * 32
DEPLOYER_SECRET = "b2" * 32
OPERATIONS_SECRET = "c3" * 32
REPORTER = Ed25519Signer.from_seed(bytes(range(32)))
ASSET = "eip155:1:0x" + "11" * 20


def address(secret: str) -> str:
    return Account.from_key(bytes.fromhex(secret)).address


def workspace(tmp_path: Path) -> Path:
    """A manifest and a signed report on disk, exactly as a service would find them."""
    root = tmp_path / "service"
    root.mkdir()
    publisher = address(PUBLISHER_SECRET)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "network": "hardhat-local",
                "chain_id": 31337,
                "rpc_url": "http://127.0.0.1:8545",
                "registry_address": Web3.to_checksum_address("0x" + "ab" * 20),
                "registry_runtime_bytecode_sha256": "cd" * 32,
                "publisher_address": publisher,
                "publisher_identity_address": publisher,
                "deployer_address": address(DEPLOYER_SECRET),
                "operations_address": address(OPERATIONS_SECRET),
                "confirmations": 1,
                "deployment_block": 3,
                "reporting_keys": [
                    {
                        "kid": REPORTER.kid,
                        "public_key": REPORTER.public_key_record()["public_key"],
                        "state": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "signed_report.json").write_text(
        json.dumps(
            REPORTER.sign_report(
                {
                    "asset_key": ASSET,
                    "control_set_root": "22" * 32,
                    "correction_of": None,
                    "evidence_root": "33" * 32,
                    "observed_at": "2026-08-13T14:16:17Z",
                    "publisher_kid": REPORTER.kid,
                    "sequence": 1,
                    "state": "CONFIRMED",
                    "state_transition": {
                        "as_of": "2026-08-13",
                        "evidence_deadline": "2026-08-13",
                    },
                    "valid_until": "2026-08-13T23:59:59Z",
                }
            )
        ),
        encoding="utf-8",
    )
    return root


def run_child(mode: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHILD), mode, str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def chain_of(root: Path) -> dict:
    return json.loads((root / "chain.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mode",
    [
        "die-before-broadcast",
        "die-after-broadcast",
        "die-after-finalize",
        "die-in-slot-after-finalize",
    ],
)
def test_a_killed_publication_is_finished_exactly_once_on_restart(
    tmp_path: Path, mode: str
) -> None:
    root = workspace(tmp_path)

    killed = run_child(mode, root)
    assert killed.returncode == 9, f"the child was meant to die: {killed.stderr}"
    assert (root / "operations" / "operation.json").exists(), (
        "the operation must survive the crash, or the restart has nothing to finish"
    )

    restarted = run_child("resolve", root)
    assert restarted.returncode == 0, restarted.stderr
    result = json.loads(restarted.stdout)

    assert result["resolved"] is True
    assert result["sequences"] == [1], "exactly one report reached the registry"
    assert result["operation_cleared"] is True
    assert result["log_entries"] == 1, "and exactly one entry records it"
    assert result["state_sequence"] == 1, (
        "the projection caught up with the publication it was separated from"
    )
    assert result["open_incidents"] == 0, "and nothing is left recorded as stuck"


def test_the_restart_reuses_the_signed_bytes_rather_than_signing_again(
    tmp_path: Path,
) -> None:
    """One report, one distinct signed transaction — across a process boundary."""
    root = workspace(tmp_path)
    run_child("die-before-broadcast", root)
    journalled = json.loads((root / "pending.json").read_text(encoding="utf-8"))

    run_child("resolve", root)

    chain = chain_of(root)
    assert len(chain["receipts"]) == 1
    assert journalled["transaction_hash"] in chain["receipts"], (
        "the transaction that landed is the one the killed process had already signed"
    )
    assert len(chain["intents"]) == 1, "no second transaction was ever prepared"


def test_resolving_when_nothing_was_in_flight_does_nothing(tmp_path: Path) -> None:
    root = workspace(tmp_path)

    result = run_child("resolve", root)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["resolved"] is False
    assert not (root / "chain.json").exists(), "nothing was published"


def test_a_second_restart_after_a_resolved_publication_is_a_no_op(
    tmp_path: Path,
) -> None:
    """Restarting repeatedly must not accumulate publications."""
    root = workspace(tmp_path)
    run_child("die-after-broadcast", root)
    run_child("resolve", root)

    again = run_child("resolve", root)

    assert json.loads(again.stdout)["resolved"] is False
    assert len(chain_of(root)["receipts"]) == 1


def test_a_crash_after_the_publisher_finalized_still_leaves_one_report(
    tmp_path: Path,
) -> None:
    """The gap between the two layers, which the earlier crash modes never reached.

    The chain accepted it, the transparency log recorded it and the publisher cleared its
    own journal — and only then did the process die, with this service's operation still
    on disk. The restart must recognise that as finished rather than publish it again.
    """
    root = workspace(tmp_path)

    killed = run_child("die-after-finalize", root)
    assert killed.returncode == 9, killed.stderr
    assert not (root / "pending.json").exists(), (
        "the publisher had already cleared its own journal before the crash"
    )
    assert (root / "operations" / "operation.json").exists(), (
        "and only this service's operation was left behind"
    )
    assert len(json.loads((root / "transparency.jsonl").read_bytes().splitlines()[0])) > 0

    restarted = run_child("resolve", root)
    result = json.loads(restarted.stdout)

    assert result["sequences"] == [1], "no second publication"
    assert result["operation_cleared"] is True
    assert result["state_sequence"] == 1
    assert len(chain_of(root)["receipts"]) == 1


def test_a_clean_slot_publishes_once_and_records_its_state(tmp_path: Path) -> None:
    """The ordinary path through the slot, end to end, in its own process."""
    root = workspace(tmp_path)

    result = run_child("slot", root)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["published"] is True
    assert len(chain_of(root)["receipts"]) == 1
    assert not (root / "operations" / "operation.json").exists()
