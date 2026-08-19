"""Fail-closed reads across two independently configured JSON-RPC endpoints."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from urllib.parse import urlsplit

from touchstone.oracles import HTTPRPC, RPC


class QuorumError(RuntimeError):
    """Base class for quorum read failures."""


class QuorumConfigurationError(QuorumError):
    """The configured endpoint set cannot provide an independent quorum."""


class QuorumUnavailable(QuorumError):
    """One or more endpoints did not provide a usable answer."""


class QuorumDisagreement(QuorumError):
    """The independent endpoints answered differently."""


class QuorumRPC:
    """Require identical JSON-RPC results from two or more independent endpoints."""

    def __init__(self, endpoints: Sequence[str], *, timeout: float = 20.0) -> None:
        values = tuple(endpoints)
        if len(values) < 2:
            raise QuorumConfigurationError("quorum needs at least two endpoints")
        hosts: list[str] = []
        for endpoint in values:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise QuorumConfigurationError(
                    "quorum endpoints must be distinct HTTPS URLs without credentials"
                )
            hosts.append(parsed.netloc.lower())
        if len(set(hosts)) != len(hosts):
            raise QuorumConfigurationError(
                "quorum endpoints must use distinct RPC hosts"
            )
        self._readers: tuple[RPC, ...] = tuple(
            HTTPRPC(endpoint, timeout=timeout) for endpoint in values
        )
        self._hosts = tuple(hosts)

    @classmethod
    def from_env(cls, *, timeout: float = 20.0) -> QuorumRPC | None:
        raw = os.environ.get("TOUCHSTONE_RPC_QUORUM")
        if raw is None or not raw.strip():
            return None
        return cls(
            tuple(item.strip() for item in raw.split(",") if item.strip()),
            timeout=timeout,
        )

    @classmethod
    def from_readers(cls, readers: Sequence[RPC]) -> QuorumRPC:
        """Build an in-memory quorum for tests and embedded callers."""
        if len(readers) < 2:
            raise QuorumConfigurationError("quorum needs at least two readers")
        instance = object.__new__(cls)
        instance._readers = tuple(readers)
        instance._hosts = tuple(f"reader-{index}" for index in range(len(readers)))
        return instance

    def call(self, method: str, params: list[object]) -> object:
        if not isinstance(method, str) or not method:
            raise ValueError("quorum method must be non-empty text")
        values: list[object] = []
        failures: list[str] = []
        for host, reader in zip(self._hosts, self._readers):
            try:
                values.append(reader.call(method, params))
            except Exception as error:  # noqa: BLE001 - provider failures are indeterminate
                failures.append(f"{host}: {type(error).__name__}")
        if failures:
            raise QuorumUnavailable(
                f"quorum method {method} did not answer at every endpoint "
                f"({', '.join(failures)})"
            )
        encoded = [
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            for value in values
        ]
        if len(set(encoded)) != 1:
            raise QuorumDisagreement(f"quorum method {method} returned disagreement")
        return values[0]
