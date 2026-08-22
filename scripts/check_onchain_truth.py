"""Compare every repository publication history with the V1 registries.

The comparison is deliberately asymmetric. A repository sequence greater than the
registry sequence is a false public claim and always fails. The registry may be ahead
because publishing and site builds are separate operations, but only by three total
publications across all checked histories. Three is one complete USTB publication cycle:
the asset verdict plus its two policy verdicts. A fourth publication means the public
projection has missed more than that declared build lag and fails.

Every registry read is made at one explicit block against both configured providers.
Both providers must identify the expected chain and return byte-identical report data.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Protocol

from web3 import Web3

from touchstone.oracles import HTTPRPC


ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "site2" / "data" / "stats.json"
FACTS = ROOT / "site2" / "_data" / "facts.json"
MAX_CHAIN_AHEAD_PUBLICATIONS = 3
STATUS_NAMES = ("CONFIRMED", "STALE", "INCONSISTENT", "UNVERIFIABLE")
GET_LATEST_REPORT_SELECTOR = "0x4def3188"

_ENDPOINTS = {
    "mainnet": ("https://xlayerrpc.okx.com", "https://rpc.xlayer.tech"),
    "testnet": (
        "https://xlayertestrpc.okx.com/terigon",
        "https://testrpc.xlayer.tech/terigon",
    ),
}
_NETWORK_NOTES = {
    "xlayer-mainnet-v1": "mainnet",
    "xlayer-testnet-v1": "testnet",
}


class RPC(Protocol):
    def call(self, method: str, params: list[object]) -> object: ...


class OnchainTruthError(RuntimeError):
    """The repository or registry evidence cannot support a truth decision."""


@dataclass(frozen=True, slots=True)
class Network:
    name: str
    chain_id: int
    registry: str
    endpoints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicationTruth:
    chain_id: int
    asset_key: str
    sequence: int
    status: str


@dataclass(frozen=True, slots=True)
class ChainTruth:
    chain_id: int
    asset_key: str
    sequence: int
    status: str
    block_number: int


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    failures: list[str]
    chain_ahead: int


def _document(path: Path, name: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OnchainTruthError(f"{name} is unavailable or invalid JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise OnchainTruthError(f"{name} must be an object")
    return value


def _hex_quantity(value: object, name: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise OnchainTruthError(f"{name} is not a hex quantity")
    try:
        parsed = int(value, 16)
    except ValueError as error:
        raise OnchainTruthError(f"{name} is not a hex quantity") from error
    if parsed < 0:
        raise OnchainTruthError(f"{name} is negative")
    return parsed


def configured_networks(path: Path = FACTS) -> list[Network]:
    facts = _document(path, "site facts")
    networks: list[Network] = []
    for identifier in ("mainnet", "testnet"):
        record = facts.get(identifier)
        if not isinstance(record, Mapping):
            raise OnchainTruthError(f"site facts have no {identifier} network")
        try:
            chain_id = int(record["chain_id"])
            registry = record["registry_v1"]
        except (KeyError, TypeError, ValueError) as error:
            raise OnchainTruthError(
                f"site facts have invalid {identifier} registry identity"
            ) from error
        if not isinstance(registry, str) or not Web3.is_address(registry):
            raise OnchainTruthError(
                f"site facts have invalid {identifier} registry address"
            )
        networks.append(
            Network(
                name=f"X Layer {identifier}",
                chain_id=chain_id,
                registry=Web3.to_checksum_address(registry),
                endpoints=_ENDPOINTS[identifier],
            )
        )
    return networks


def _derived_identities(document: Mapping[str, object]) -> list[tuple[int, str]]:
    reports = document.get("reports")
    assets = document.get("assets")
    policies = document.get("policies")
    if not isinstance(reports, list) or any(
        not isinstance(record, Mapping) for record in reports
    ):
        raise OnchainTruthError("stats.json must contain a reports array of objects")
    if not isinstance(assets, list) or any(
        not isinstance(record, Mapping) for record in assets
    ):
        raise OnchainTruthError("stats.json must contain an assets array of objects")
    if not isinstance(policies, list) or any(
        not isinstance(record, Mapping) for record in policies
    ):
        raise OnchainTruthError("stats.json must contain a policies array of objects")

    asset_keys: dict[str, str] = {}
    for record in assets:
        ticker = record.get("ticker")
        key = record.get("canonical_asset_key", record.get("asset_key"))
        if not isinstance(ticker, str) or not isinstance(key, str):
            raise OnchainTruthError("stats.json asset identity is incomplete")
        asset_keys[ticker.lower()] = key

    policy_versions: dict[str, int] = {}
    for record in policies:
        policy_id = record.get("policy_id")
        version = record.get("version")
        if not isinstance(policy_id, str) or type(version) is not int or version < 1:
            raise OnchainTruthError("stats.json policy identity is incomplete")
        policy_versions[policy_id] = version

    network_at: dict[str, str] = {}
    for record in reports:
        note = record.get("note")
        observed_at = record.get("observed_at")
        network = _NETWORK_NOTES.get(note)
        if network is not None and isinstance(observed_at, str):
            previous = network_at.setdefault(observed_at, network)
            if previous != network:
                raise OnchainTruthError(
                    f"publication time {observed_at} maps to multiple chains"
                )

    identities: list[tuple[int, str]] = []
    network_ids = {"mainnet": 196, "testnet": 1952}
    for record in reports:
        epoch_id = record.get("epoch_id")
        if not isinstance(epoch_id, str) or "-" not in epoch_id:
            raise OnchainTruthError("publication epoch_id does not identify an asset")
        ticker = epoch_id.split("-", 1)[0].lower()
        base_key = asset_keys.get(ticker)
        if base_key is None:
            raise OnchainTruthError(f"publication epoch {epoch_id} identifies no asset")

        note = record.get("note")
        network = _NETWORK_NOTES.get(note)
        if network is None:
            observed_at = record.get("observed_at")
            network = network_at.get(str(observed_at))
        if network is None:
            raise OnchainTruthError(
                f"publication epoch {epoch_id} has no derivable publication chain"
            )

        policy = record.get("policy")
        key = base_key
        if policy is not None:
            if not isinstance(policy, str) or policy not in policy_versions:
                raise OnchainTruthError(
                    f"publication epoch {epoch_id} has an unknown policy"
                )
            key = f"{base_key}#policy:{policy}:{policy_versions[policy]}"
        identities.append((network_ids[network], key))
    return identities


def load_publication_claims(path: Path = STATS) -> list[PublicationTruth]:
    document = _document(path, "publication statistics")
    reports = document.get("reports")
    assert isinstance(reports, list)
    if document.get("reports_published") != len(reports):
        raise OnchainTruthError(
            "stats.json reports_published does not equal its publication row count"
        )
    derived = _derived_identities(document)
    histories: dict[tuple[int, str], dict[int, str]] = {}
    for index, (record, expected) in enumerate(zip(reports, derived), start=1):
        assert isinstance(record, Mapping)
        chain_id = record.get("chain_id")
        asset_key = record.get("asset_key")
        if (chain_id, asset_key) != expected:
            raise OnchainTruthError(
                f"stats.json report row {index} chain_id/asset_key is "
                f"{(chain_id, asset_key)!r}; derived identity is {expected!r}"
            )
        sequence = record.get("sequence")
        status = record.get("state")
        if type(sequence) is not int or sequence < 1:
            raise OnchainTruthError(f"stats.json report row {index} has invalid sequence")
        if status not in STATUS_NAMES:
            raise OnchainTruthError(f"stats.json report row {index} has invalid state")
        history = histories.setdefault(expected, {})
        if sequence in history:
            raise OnchainTruthError(
                f"stats.json duplicates chain {chain_id} key {asset_key} sequence {sequence}"
            )
        history[sequence] = str(status)

    claims: list[PublicationTruth] = []
    for (chain_id, asset_key), history in sorted(histories.items()):
        latest = max(history)
        if set(history) != set(range(1, latest + 1)):
            raise OnchainTruthError(
                f"stats.json chain {chain_id} key {asset_key} has a sequence gap"
            )
        claims.append(
            PublicationTruth(chain_id, asset_key, latest, history[latest])
        )
    return claims


def decode_latest_report(value: object) -> tuple[int, str]:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise OnchainTruthError("getLatestReport return data is not hex")
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError as error:
        raise OnchainTruthError("getLatestReport return data is not hex") from error
    if len(raw) < 320 or len(raw) % 32:
        raise OnchainTruthError("getLatestReport return data is malformed or short")
    words = [int.from_bytes(raw[offset : offset + 32], "big") for offset in range(0, 320, 32)]
    if words[0] != 32 or words[9] != 288:
        raise OnchainTruthError("getLatestReport return data has an invalid tuple layout")
    status = words[4]
    sequence = words[8]
    if status >= len(STATUS_NAMES) or sequence > (1 << 64) - 1:
        raise OnchainTruthError("getLatestReport return data has invalid field values")
    text_length = int.from_bytes(raw[320:352], "big")
    padded_length = (text_length + 31) // 32 * 32
    if len(raw) != 352 + padded_length:
        raise OnchainTruthError("getLatestReport return data has an invalid reportURI")
    try:
        raw[352 : 352 + text_length].decode("utf-8")
    except UnicodeDecodeError as error:
        raise OnchainTruthError(
            "getLatestReport return data has an invalid reportURI"
        ) from error
    return sequence, STATUS_NAMES[status]


def read_network(
    network: Network,
    asset_keys: Sequence[str],
    *,
    readers: Sequence[RPC] | None = None,
) -> list[ChainTruth]:
    if len(network.endpoints) < 2:
        raise OnchainTruthError(f"chain {network.chain_id} needs at least two endpoints")
    if readers is None:
        readers = tuple(HTTPRPC(endpoint, timeout=20) for endpoint in network.endpoints)
    if len(readers) != len(network.endpoints):
        raise OnchainTruthError(f"chain {network.chain_id} reader count is invalid")

    heads: list[int] = []
    for endpoint, reader in zip(network.endpoints, readers):
        try:
            chain_id = _hex_quantity(reader.call("eth_chainId", []), "chain id")
            head = _hex_quantity(reader.call("eth_blockNumber", []), "block number")
        except Exception as error:
            if isinstance(error, OnchainTruthError):
                raise
            raise OnchainTruthError(
                f"chain {network.chain_id} endpoint {endpoint} did not answer: "
                f"{type(error).__name__}"
            ) from error
        if chain_id != network.chain_id:
            raise OnchainTruthError(
                f"endpoint {endpoint} reports chain {chain_id}, expected {network.chain_id}"
            )
        heads.append(head)
    pinned_block = min(heads)
    block = hex(pinned_block)

    readings: list[ChainTruth] = []
    for asset_key in sorted(asset_keys):
        calldata = GET_LATEST_REPORT_SELECTOR + Web3.keccak(text=asset_key).hex()
        answers: list[object] = []
        for endpoint, reader in zip(network.endpoints, readers):
            try:
                answers.append(
                    reader.call(
                        "eth_call",
                        [{"to": network.registry, "data": calldata}, block],
                    )
                )
            except Exception as error:
                raise OnchainTruthError(
                    f"chain {network.chain_id} endpoint {endpoint} did not answer "
                    f"getLatestReport at block {pinned_block}: {type(error).__name__}"
                ) from error
        if any(answer != answers[0] for answer in answers[1:]):
            raise OnchainTruthError(
                f"chain {network.chain_id} endpoints DISAGREE on key {asset_key} "
                f"at block {pinned_block}"
            )
        sequence, status = decode_latest_report(answers[0])
        readings.append(
            ChainTruth(
                network.chain_id,
                asset_key,
                sequence,
                status,
                pinned_block,
            )
        )
    return readings


def compare_truth(
    site: Sequence[PublicationTruth],
    chain: Sequence[PublicationTruth | ChainTruth],
    *,
    max_chain_ahead: int,
) -> ComparisonResult:
    if type(max_chain_ahead) is not int or max_chain_ahead < 0:
        raise ValueError("max_chain_ahead must be a non-negative integer")
    site_by_key = {(item.chain_id, item.asset_key): item for item in site}
    chain_by_key = {(item.chain_id, item.asset_key): item for item in chain}
    failures: list[str] = []
    for key in sorted(site_by_key.keys() - chain_by_key.keys()):
        failures.append(f"chain {key[0]} key {key[1]}: no registry reading")
    for key in sorted(chain_by_key.keys() - site_by_key.keys()):
        failures.append(f"chain {key[0]} key {key[1]}: no repository history")

    chain_ahead = 0
    for key in sorted(site_by_key.keys() & chain_by_key.keys()):
        claimed = site_by_key[key]
        proven = chain_by_key[key]
        if claimed.sequence > proven.sequence:
            failures.append(
                f"chain {key[0]} key {key[1]}: FATAL site claims sequence "
                f"{claimed.sequence}, chain proves only {proven.sequence}"
            )
        elif claimed.sequence == proven.sequence and claimed.status != proven.status:
            failures.append(
                f"chain {key[0]} key {key[1]}: sequence {claimed.sequence} status "
                f"site={claimed.status} chain={proven.status}"
            )
        else:
            chain_ahead += proven.sequence - claimed.sequence
    if chain_ahead > max_chain_ahead:
        failures.append(
            f"chain is ahead by {chain_ahead} publications; declared allowance is "
            f"{max_chain_ahead}"
        )
    return ComparisonResult(failures=failures, chain_ahead=chain_ahead)


def _print_readings(
    site: Sequence[PublicationTruth], chain: Sequence[ChainTruth]
) -> None:
    site_by_key = {(item.chain_id, item.asset_key): item for item in site}
    for reading in sorted(chain, key=lambda item: (item.chain_id, item.asset_key)):
        claimed = site_by_key[(reading.chain_id, reading.asset_key)]
        delta = reading.sequence - claimed.sequence
        verdict = "EXACT" if delta == 0 else f"CHAIN AHEAD BY {delta} (ALLOWED LAG)"
        if delta < 0:
            verdict = "FATAL: SITE CLAIMS MORE THAN CHAIN"
        elif delta == 0 and reading.status != claimed.status:
            verdict = "FAIL: STATUS DISAGREEMENT"
        print(
            f"chain={reading.chain_id} block={reading.block_number} "
            f"key={reading.asset_key} chain_sequence={reading.sequence} "
            f"chain_status={reading.status} site_sequence={claimed.sequence} "
            f"site_status={claimed.status} verdict={verdict}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the registry comparison and exit successfully",
    )
    parser.add_argument("--stats", type=Path, default=STATS)
    parser.add_argument("--facts", type=Path, default=FACTS)
    arguments = parser.parse_args(argv)

    if arguments.offline:
        print(
            "on-chain truth network check skipped (--offline); "
            "no registry claim was verified"
        )
        return 0

    try:
        claims = load_publication_claims(arguments.stats)
        networks = configured_networks(arguments.facts)
        readings: list[ChainTruth] = []
        for network in networks:
            keys = [
                claim.asset_key
                for claim in claims
                if claim.chain_id == network.chain_id
            ]
            if not keys:
                raise OnchainTruthError(
                    f"stats.json has no publication histories for chain {network.chain_id}"
                )
            readings.extend(read_network(network, keys))
        result = compare_truth(
            claims, readings, max_chain_ahead=MAX_CHAIN_AHEAD_PUBLICATIONS
        )
    except (OnchainTruthError, ValueError) as error:
        print(f"ONCHAIN TRUTH FAIL: {error}", file=sys.stderr)
        return 1

    _print_readings(claims, readings)
    print(
        "ASYMMETRIC RULE: site > chain is always fatal; chain > site is build lag "
        f"allowed only up to {MAX_CHAIN_AHEAD_PUBLICATIONS} aggregate publications."
    )
    if result.failures:
        print("ONCHAIN TRUTH FAIL")
        for failure in result.failures:
            print(f"  {failure}")
        return 1
    print(
        f"ONCHAIN TRUTH PASS: chain is ahead by {result.chain_ahead} publication(s) "
        f"within the declared allowance of {MAX_CHAIN_AHEAD_PUBLICATIONS}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
