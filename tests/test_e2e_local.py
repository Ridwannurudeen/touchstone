import pytest

from scripts.e2e_local import run_managed_e2e


@pytest.mark.local_e2e
def test_complete_loop_against_a_managed_local_hardhat_node() -> None:
    """The loop starts and stops its own clock-pinned node, so it never self-skips."""
    result = run_managed_e2e()

    assert result["asset_gate_initial"] == "allowed"
    assert result["asset_gate_after_age"] == "observation too old"
    assert result["historical_sequence"] == 1
    assert result["log_entries"] == 2
    assert result["first_transaction"] != result["correction_transaction"]
