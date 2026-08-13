from web3 import Web3
import pytest

from scripts.e2e_local import RPC_URL, run_e2e


@pytest.mark.local_e2e
def test_complete_loop_against_running_local_hardhat_node() -> None:
    if not Web3(Web3.HTTPProvider(RPC_URL)).is_connected():
        pytest.skip(
            "LOCAL E2E SKIPPED: no Hardhat node at http://127.0.0.1:8545; "
            "run `python scripts/e2e_local.py` for the managed full loop"
        )

    result = run_e2e()
    assert result["asset_gate_initial"] == "allowed"
    assert result["asset_gate_after_age"] == "observation too old"
    assert result["historical_sequence"] == 1
    assert result["log_entries"] == 2
