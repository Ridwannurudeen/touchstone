"""Pin the deterministic adversarial compiler benchmark and its honest metrics."""

from scripts.ai_benchmark import run_benchmark


def test_deterministic_ai_boundary_benchmark() -> None:
    summary = run_benchmark()
    assert summary["benchmark"] == "deterministic-compiler-boundary-v1"
    assert summary["total_cases"] == 40
    assert summary["counts"] == {"accepted": 8, "abstained": 6, "rejected": 26}
    assert summary["abstention_rate"] == 0.15
    assert summary["injection_rejection_rate"] == 1.0
    assert summary["exact_span_cases"] == 8
    assert summary["exact_span_gate_passed"] == 8
