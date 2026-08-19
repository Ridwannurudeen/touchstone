"""Independent RPC quorum refuses disagreement and unavailable providers."""

from __future__ import annotations

import pytest

from touchstone.rpc_quorum import (
    QuorumConfigurationError,
    QuorumDisagreement,
    QuorumRPC,
    QuorumUnavailable,
)


class Reader:
    def __init__(self, value: object = "0x7a", error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.calls: list[str] = []

    def call(self, method: str, params: list[object]) -> object:
        del params
        self.calls.append(method)
        if self.error is not None:
            raise self.error
        return self.value


def test_quorum_returns_identical_result_and_calls_every_reader() -> None:
    first = Reader({"number": 1, "hash": "0xabc"})
    second = Reader({"hash": "0xabc", "number": 1})
    quorum = QuorumRPC.from_readers((first, second))

    assert quorum.call("eth_chainId", []) == {"number": 1, "hash": "0xabc"}
    assert first.calls == ["eth_chainId"]
    assert second.calls == ["eth_chainId"]


def test_quorum_refuses_disagreement() -> None:
    quorum = QuorumRPC.from_readers((Reader("0x1"), Reader("0x2")))

    with pytest.raises(QuorumDisagreement, match="disagreement"):
        quorum.call("eth_blockNumber", [])


def test_quorum_refuses_partial_availability() -> None:
    quorum = QuorumRPC.from_readers(
        (Reader("0x1"), Reader(error=TimeoutError("offline")))
    )

    with pytest.raises(QuorumUnavailable, match="did not answer"):
        quorum.call("eth_chainId", [])


@pytest.mark.parametrize(
    "endpoints",
    [
        ("https://one.example/rpc",),
        ("http://one.example/rpc", "https://two.example/rpc"),
        ("https://one.example/rpc", "https://one.example/other"),
        ("https://user:secret@one.example/rpc", "https://two.example/rpc"),
    ],
)
def test_quorum_requires_independent_https_endpoints(endpoints: tuple[str, ...]) -> None:
    with pytest.raises(QuorumConfigurationError):
        QuorumRPC(endpoints)
