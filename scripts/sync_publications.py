"""Retain verified publisher bundles and append receipt-derived publication rows."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Protocol

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_onchain_truth import (  # noqa: E402
    Network,
    OnchainTruthError,
    configured_networks,
)
from touchstone.rpc_quorum import QuorumRPC  # noqa: E402
from touchstone.locking import LockUnavailable, exclusive_lock  # noqa: E402
from touchstone.publish import asset_key_bytes, epoch_key_bytes  # noqa: E402
from touchstone.signing import canonical_json_bytes, strict_json_loads  # noqa: E402
from touchstone.translog import (  # noqa: E402
    TransparencyLog,
    TransparencyLogError,
)
from touchstone.ustb_daemon import EpochProductionError, _bundle_name  # noqa: E402
from touchstone.verify import VerificationError, verify_bundle  # noqa: E402


BUNDLES = ROOT / "site2" / "data"
STATS = BUNDLES / "stats.json"
FACTS = ROOT / "site2" / "_data" / "facts.json"
_NETWORK_NOTES = {
    196: "xlayer-mainnet-v1",
    1952: "xlayer-testnet-v1",
}
_ROW_FIELDS = {
    "chain_id",
    "asset_key",
    "note",
    "epoch_id",
    "sequence",
    "state",
    "policy",
    "correction_of",
    "transaction_hash",
    "block",
    "observed_at",
}
_STATUS_NAMES = ("CONFIRMED", "STALE", "INCONSISTENT", "UNVERIFIABLE")
_PUBLISH_TYPES = (
    "bytes32",
    "bytes32",
    "bytes32",
    "bytes32",
    "uint8",
    "uint64",
    "uint64",
    "uint64",
    "string",
)
_CORRECTION_TYPES = (_PUBLISH_TYPES[0], "uint64", *_PUBLISH_TYPES[1:])
_PUBLISH_SELECTOR = Web3.keccak(
    text="publish(bytes32,bytes32,bytes32,bytes32,uint8,uint64,uint64,uint64,string)"
)[:4]
_CORRECTION_SELECTOR = Web3.keccak(
    text=(
        "publishCorrection(bytes32,uint64,bytes32,bytes32,bytes32,uint8,uint64,"
        "uint64,uint64,string)"
    )
)[:4]


class RPC(Protocol):
    def call(self, method: str, params: list[object]) -> object: ...


class PublicationSyncError(RuntimeError):
    """Publisher evidence cannot be appended without changing existing history."""


@dataclass(frozen=True, slots=True)
class Candidate:
    raw: bytes
    report: Mapping[str, object]
    entry: Mapping[str, object]


def _document(path: Path, name: str) -> dict[str, object]:
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicationSyncError(f"{name} is unavailable or invalid: {error}") from error
    if not isinstance(value, dict):
        raise PublicationSyncError(f"{name} must be an object")
    return value


def _reports(document: Mapping[str, object]) -> list[dict[str, object]]:
    reports = document.get("reports")
    if not isinstance(reports, list) or any(not isinstance(row, dict) for row in reports):
        raise PublicationSyncError("stats.json must contain a reports array of objects")
    return reports


def _policy_id(report: Mapping[str, object]) -> str | None:
    policy = report.get("policy")
    if policy is None:
        return None
    if not isinstance(policy, Mapping) or not isinstance(policy.get("policy_id"), str):
        raise PublicationSyncError("verified policy report has no policy_id")
    return str(policy["policy_id"])


def _report_key(report: Mapping[str, object]) -> tuple[object, ...]:
    try:
        return (
            str(report["asset_key"]).split("#policy:", 1)[0],
            report["epoch_id"],
            report["sequence"],
            report["state"],
            report["correction_of"],
            report["observed_at"],
            _policy_id(report),
        )
    except KeyError as error:
        raise PublicationSyncError(
            f"verified report is missing {error.args[0]}"
        ) from error


def _row_key(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        str(row.get("asset_key", "")).split("#policy:", 1)[0],
        row.get("epoch_id"),
        row.get("sequence"),
        row.get("state"),
        row.get("correction_of"),
        row.get("observed_at"),
        row.get("policy"),
    )


def check_retained_publications(bundles: Path = BUNDLES, stats: Path = STATS) -> int:
    """Verify every retained bundle and require exactly one publication row."""
    document = _document(stats, "stats.json")
    reports = _reports(document)
    checked = 0
    for path in sorted(bundles.glob("*.json")):
        if path.resolve() == stats.resolve():
            continue
        try:
            report = verify_bundle(path.read_bytes())
        except (OSError, TypeError, ValueError, VerificationError) as error:
            raise PublicationSyncError(
                f"retained bundle {path.name} does not verify: {error}"
            ) from error
        matches = [row for row in reports if _row_key(row) == _report_key(report)]
        if not matches:
            raise PublicationSyncError(
                f"retained bundle {path.name} has no publication row"
            )
        if len(matches) != 1:
            raise PublicationSyncError(
                f"retained bundle {path.name} has {len(matches)} publication rows"
            )
        checked += 1
    return checked


def _source_candidates(source: Path) -> list[Candidate]:
    if not source.is_dir():
        raise PublicationSyncError(f"publication source is not a directory: {source}")
    entries: dict[bytes, Mapping[str, object]] = {}
    logs = sorted(source.rglob("transparency.jsonl"))
    if not logs:
        raise PublicationSyncError(f"no transparency logs under {source}")
    for path in logs:
        try:
            verified = TransparencyLog(path).verify()
        except (ValueError, TransparencyLogError) as error:
            raise PublicationSyncError(f"{path} does not verify: {error}") from error
        for entry in verified:
            key = canonical_json_bytes(entry["signed_report"])
            previous = entries.setdefault(key, entry)
            if previous != entry:
                raise PublicationSyncError(
                    "multiple transparency entries carry the same signed report"
                )

    candidates: list[Candidate] = []
    seen: set[bytes] = set()
    paths = sorted(
        path
        for path in source.rglob("*.json")
        if path.parent.name == "bundles"
    )
    if not paths:
        raise PublicationSyncError(f"no publisher bundles under {source}")
    for path in paths:
        try:
            raw = path.read_bytes()
            parsed = strict_json_loads(raw)
            report = verify_bundle(raw)
        except (OSError, TypeError, ValueError, VerificationError) as error:
            raise PublicationSyncError(
                f"publisher bundle {path} does not verify offline: {error}"
            ) from error
        if not isinstance(parsed, Mapping):
            raise PublicationSyncError(f"publisher bundle {path} must be an object")
        signed = parsed.get("signed_report")
        if not isinstance(signed, Mapping):
            raise PublicationSyncError(f"publisher bundle {path} has no signed report")
        key = canonical_json_bytes(signed)
        entry = entries.get(key)
        if entry is None:
            raise PublicationSyncError(
                f"publisher bundle {path} has no matching transparency entry"
            )
        if key in seen:
            raise PublicationSyncError(
                f"publisher source contains duplicate bundle for {_report_key(report)!r}"
            )
        seen.add(key)
        candidates.append(Candidate(raw=raw, report=report, entry=entry))
    return candidates


def _hex_quantity(value: object, name: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise PublicationSyncError(f"chain receipt {name} is not a hex quantity")
    try:
        return int(value, 16)
    except ValueError as error:
        raise PublicationSyncError(
            f"chain receipt {name} is not a hex quantity"
        ) from error


def _publication(candidate: Candidate) -> tuple[str, Mapping[str, object]]:
    publication = candidate.entry.get("publication")
    if not isinstance(publication, Mapping):
        raise PublicationSyncError("transparency entry has no publication")
    transaction_hash = publication.get("transaction_hash")
    receipt = publication.get("receipt")
    if not isinstance(transaction_hash, str) or not isinstance(receipt, Mapping):
        raise PublicationSyncError("transparency entry has an invalid publication")
    return transaction_hash, receipt


def _validate_receipt(
    transaction_hash: str,
    logged: Mapping[str, object],
    receipt: object,
) -> None:
    if not isinstance(receipt, Mapping):
        raise PublicationSyncError(f"chain receipt for {transaction_hash} is invalid")
    expected = {
        "transaction_hash": transaction_hash,
        "block_hash": logged.get("block_hash"),
        "block_number": logged.get("block_number"),
        "effective_gas_price": logged.get("effective_gas_price"),
        "gas_used": logged.get("gas_used"),
        "status": logged.get("status"),
    }
    actual = {
        "transaction_hash": str(receipt.get("transactionHash", "")).lower(),
        "block_hash": str(receipt.get("blockHash", "")).lower(),
        "block_number": _hex_quantity(receipt.get("blockNumber"), "block number"),
        "effective_gas_price": _hex_quantity(
            receipt.get("effectiveGasPrice"), "effective gas price"
        ),
        "gas_used": _hex_quantity(receipt.get("gasUsed"), "gas used"),
        "status": _hex_quantity(receipt.get("status"), "status"),
    }
    if actual != expected or actual["status"] != 1:
        raise PublicationSyncError(
            f"chain receipt for {transaction_hash} disagrees with the transparency log"
        )


def _unix_timestamp(value: object, name: str) -> int:
    if not isinstance(value, str):
        raise PublicationSyncError(f"report {name} is not a UTC timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicationSyncError(f"report {name} is not a UTC timestamp") from error
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0:
        raise PublicationSyncError(f"report {name} is not a UTC timestamp")
    return int(moment.timestamp())


def _validate_transaction(
    transaction_hash: str,
    report: Mapping[str, object],
    network: Network,
    transaction: object,
) -> None:
    if not isinstance(transaction, Mapping):
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} is unavailable"
        )
    if str(transaction.get("hash", "")).lower() != transaction_hash:
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} reports a different hash"
        )
    if str(transaction.get("to", "")).lower() != network.registry.lower():
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} has the wrong destination"
        )
    calldata = transaction.get("input")
    if not isinstance(calldata, str) or not calldata.startswith("0x"):
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} has invalid calldata"
        )
    try:
        raw = bytes.fromhex(calldata[2:])
    except ValueError as error:
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} has invalid calldata"
        ) from error
    correction = report.get("correction_of")
    selector = _PUBLISH_SELECTOR if correction is None else _CORRECTION_SELECTOR
    types = _PUBLISH_TYPES if correction is None else _CORRECTION_TYPES
    if raw[:4] != selector:
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} calls the wrong function"
        )
    try:
        decoded = Web3().codec.decode(types, raw[4:])
        status = _STATUS_NAMES.index(str(report["state"]))
        common = (
            asset_key_bytes(report["asset_key"]),
            bytes.fromhex(str(report["control_set_root"])),
            bytes.fromhex(str(report["evidence_root"])),
            epoch_key_bytes(report["epoch_id"]),
            status,
            _unix_timestamp(report["observed_at"], "observed_at"),
            _unix_timestamp(report["valid_until"], "valid_until"),
            report["sequence"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PublicationSyncError(
            f"verified report cannot be bound to transaction {transaction_hash}: {error}"
        ) from error
    expected = common if correction is None else (common[0], correction, *common[1:])
    if decoded[:-1] != expected or not isinstance(decoded[-1], str) or not decoded[-1]:
        raise PublicationSyncError(
            f"registry transaction {transaction_hash} does not publish the signed report"
        )


def _publication_chain(
    candidate: Candidate,
    networks: Sequence[Network],
    quorum_factory: Callable[[tuple[str, ...]], RPC],
) -> int:
    transaction_hash, logged = _publication(candidate)
    found: list[int] = []
    for network in networks:
        try:
            quorum = quorum_factory(network.endpoints)
            chain_id = _hex_quantity(quorum.call("eth_chainId", []), "chain id")
            if chain_id != network.chain_id:
                raise PublicationSyncError(
                    f"RPC reports chain {chain_id}, expected {network.chain_id}"
                )
            receipt = quorum.call("eth_getTransactionReceipt", [transaction_hash])
        except PublicationSyncError:
            raise
        except Exception as error:
            raise PublicationSyncError(
                f"dual-RPC receipt check failed for chain {network.chain_id}: "
                f"{type(error).__name__}: {error}"
            ) from error
        if receipt is None:
            continue
        _validate_receipt(transaction_hash, logged, receipt)
        try:
            transaction = quorum.call("eth_getTransactionByHash", [transaction_hash])
        except Exception as error:
            raise PublicationSyncError(
                f"dual-RPC transaction check failed for chain {network.chain_id}: "
                f"{type(error).__name__}: {error}"
            ) from error
        _validate_transaction(transaction_hash, candidate.report, network, transaction)
        found.append(network.chain_id)
    if len(found) != 1:
        raise PublicationSyncError(
            f"transaction {transaction_hash} resolved to {len(found)} publication chains"
        )
    return found[0]


def _derived_row(candidate: Candidate, chain_id: int) -> dict[str, object]:
    transaction_hash, receipt = _publication(candidate)
    policy = _policy_id(candidate.report)
    note = f"policy:{policy}" if policy is not None else _NETWORK_NOTES.get(chain_id)
    if note is None:
        raise PublicationSyncError(f"chain {chain_id} has no publication note")
    row = {
        "chain_id": chain_id,
        "asset_key": candidate.report["asset_key"],
        "note": note,
        "epoch_id": candidate.report["epoch_id"],
        "sequence": candidate.report["sequence"],
        "state": candidate.report["state"],
        "policy": policy,
        "correction_of": candidate.report["correction_of"],
        "transaction_hash": transaction_hash,
        "block": receipt.get("block_number"),
        "observed_at": candidate.report["observed_at"],
    }
    if set(row) != _ROW_FIELDS:
        raise AssertionError("derived publication row schema drifted")
    return row


def _identity(row: Mapping[str, object]) -> tuple[object, object, object]:
    return row.get("chain_id"), row.get("asset_key"), row.get("sequence")


def _encoded(document: Mapping[str, object], *, indent: int) -> bytes:
    return (json.dumps(document, indent=indent, ensure_ascii=False) + "\n").encode()


def _replace_all(files: Sequence[tuple[Path, bytes]]) -> None:
    staged: list[tuple[Path, Path]] = []
    originals = {
        target: target.read_bytes() if target.exists() else None for target, _ in files
    }
    replaced: list[Path] = []
    try:
        for target, raw in files:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
                staged.append((Path(output.name), target))
        for temporary, target in staged:
            os.replace(temporary, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            original = originals[target]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=f".{target.name}.rollback.", delete=False
                ) as output:
                    output.write(original)
                    rollback = Path(output.name)
                os.replace(rollback, target)
        raise
    finally:
        for temporary, _ in staged:
            if temporary.exists():
                temporary.unlink()


def _sync_publications(
    *,
    source: Path,
    bundles: Path = BUNDLES,
    stats: Path = STATS,
    facts: Path = FACTS,
    networks: Sequence[Network] | None = None,
    quorum_factory: Callable[[tuple[str, ...]], RPC] = QuorumRPC,
) -> list[dict[str, object]]:
    if not bundles.is_dir():
        raise PublicationSyncError(f"retained bundle directory is unavailable: {bundles}")
    stats_document = _document(stats, "stats.json")
    facts_document = _document(facts, "facts.json")
    reports = _reports(stats_document)
    candidates = _source_candidates(source)
    if networks is None:
        try:
            networks = configured_networks(facts)
        except OnchainTruthError as error:
            raise PublicationSyncError(str(error)) from error

    by_transaction: dict[object, list[dict[str, object]]] = {}
    by_identity: dict[tuple[object, object, object], dict[str, object]] = {}
    for row in reports:
        by_transaction.setdefault(row.get("transaction_hash"), []).append(row)
        identity = _identity(row)
        if identity in by_identity:
            raise PublicationSyncError(f"stats.json duplicates publication {identity!r}")
        by_identity[identity] = row

    retained_by_signed: dict[bytes, tuple[Path, bytes]] = {}
    for path in sorted(bundles.glob("*.json")):
        if path.resolve() == stats.resolve():
            continue
        try:
            raw = path.read_bytes()
            retained = strict_json_loads(raw)
            verify_bundle(raw)
        except (OSError, TypeError, ValueError, VerificationError) as error:
            raise PublicationSyncError(
                f"existing retained bundle {path.name} does not verify: {error}"
            ) from error
        if not isinstance(retained, Mapping) or not isinstance(
            retained.get("signed_report"), Mapping
        ):
            raise PublicationSyncError(
                f"existing retained bundle {path.name} has no signed report"
            )
        signed_key = canonical_json_bytes(retained["signed_report"])
        previous = retained_by_signed.setdefault(signed_key, (path, raw))
        if previous[1] != raw:
            raise PublicationSyncError(
                "multiple retained bundles for one signed report differ byte-for-byte"
            )

    new_rows: list[dict[str, object]] = []
    bundle_writes: dict[Path, bytes] = {}
    for candidate in candidates:
        transaction_hash, _ = _publication(candidate)
        matches = by_transaction.get(transaction_hash, [])
        if len(matches) > 1:
            raise PublicationSyncError(
                f"stats.json duplicates transaction {transaction_hash}"
            )
        chain_id = (
            matches[0].get("chain_id")
            if matches
            else _publication_chain(candidate, networks, quorum_factory)
        )
        if type(chain_id) is not int:
            raise PublicationSyncError(
                f"publication {transaction_hash} has no integer chain id"
            )
        row = _derived_row(candidate, chain_id)
        identity = _identity(row)
        existing = by_identity.get(identity)
        if existing is not None and existing != row:
            raise PublicationSyncError(
                f"append-only sync refuses to alter existing row {identity!r}"
            )
        if matches and matches[0] != row:
            raise PublicationSyncError(
                f"append-only sync refuses to alter existing row {identity!r}"
            )
        is_new = existing is None
        if is_new:
            new_rows.append(row)
            by_identity[identity] = row

        signed_key = canonical_json_bytes(candidate.entry["signed_report"])
        retained_match = retained_by_signed.get(signed_key)
        if retained_match is not None:
            if is_new and retained_match[1] != candidate.raw:
                raise PublicationSyncError(
                    f"new publisher bundle for {identity!r} is not retained byte-for-byte"
                )
            continue
        try:
            destination = bundles / _bundle_name(candidate.report, chain_id)
        except EpochProductionError as error:
            raise PublicationSyncError(str(error)) from error
        if destination.exists():
            try:
                retained = strict_json_loads(destination.read_bytes())
                verify_bundle(retained)
            except (OSError, TypeError, ValueError, VerificationError) as error:
                raise PublicationSyncError(
                    f"existing retained bundle {destination.name} does not verify: {error}"
                ) from error
            retained_signed = (
                retained.get("signed_report")
                if isinstance(retained, Mapping)
                else None
            )
            if retained_signed != candidate.entry.get("signed_report"):
                raise PublicationSyncError(
                    f"append-only sync refuses to overwrite {destination.name}"
                )
        else:
            previous = bundle_writes.setdefault(destination, candidate.raw)
            if previous != candidate.raw:
                raise PublicationSyncError(
                    f"publisher source conflicts on {destination.name}"
                )
            retained_by_signed[signed_key] = (destination, candidate.raw)

    new_rows.sort(key=lambda row: (int(row["block"]), str(row["transaction_hash"])))
    desired_reports = [*reports, *new_rows]
    stats_changed = bool(new_rows)
    if stats_changed:
        stats_document["reports"] = desired_reports
        stats_document["reports_published"] = len(desired_reports)
        stats_document["confirmed_reports"] = sum(
            row.get("state") == "CONFIRMED" for row in desired_reports
        )

    counts = facts_document.get("counts")
    if not isinstance(counts, dict):
        raise PublicationSyncError("facts.json must contain a counts object")
    desired_counts = {
        "reports_published": str(len(desired_reports)),
        "confirmed_reports": str(
            sum(row.get("state") == "CONFIRMED" for row in desired_reports)
        ),
        "bundles_downloadable": str(
            sum(path.name != stats.name for path in bundles.glob("*.json"))
            + len(bundle_writes)
        ),
    }
    facts_changed = any(counts.get(key) != value for key, value in desired_counts.items())
    counts.update(desired_counts)

    writes = sorted(bundle_writes.items(), key=lambda item: item[0].name)
    if stats_changed:
        writes.append((stats, _encoded(stats_document, indent=1)))
    if facts_changed:
        writes.append((facts, _encoded(facts_document, indent=2)))
    if writes:
        _replace_all(writes)
    check_retained_publications(bundles, stats)
    return new_rows


def _lock_path(stats: Path) -> Path:
    identity = hashlib.sha256(str(stats.resolve()).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / f"touchstone-publication-sync-{identity}.lock"


def sync_publications(
    *,
    source: Path,
    bundles: Path = BUNDLES,
    stats: Path = STATS,
    facts: Path = FACTS,
    networks: Sequence[Network] | None = None,
    quorum_factory: Callable[[tuple[str, ...]], RPC] = QuorumRPC,
) -> list[dict[str, object]]:
    """Serialize, validate, stage, and append publisher evidence."""
    with exclusive_lock(_lock_path(stats)):
        return _sync_publications(
            source=source,
            bundles=bundles,
            stats=stats,
            facts=facts,
            networks=networks,
            quorum_factory=quorum_factory,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--from",
        dest="source",
        type=Path,
        help="local copy of publisher workspaces fetched with read-only scp",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify retained bundles have publication rows without network access",
    )
    parser.add_argument("--bundles", type=Path, default=BUNDLES)
    parser.add_argument("--stats", type=Path, default=STATS)
    parser.add_argument("--facts", type=Path, default=FACTS)
    arguments = parser.parse_args(argv)

    try:
        if arguments.check:
            count = check_retained_publications(arguments.bundles, arguments.stats)
            print(f"PUBLICATION SYNC PASS: {count} retained bundles have publication rows")
            return 0
        rows = sync_publications(
            source=arguments.source,
            bundles=arguments.bundles,
            stats=arguments.stats,
            facts=arguments.facts,
        )
    except (LockUnavailable, PublicationSyncError, OSError) as error:
        print(f"PUBLICATION SYNC FAIL: {error}", file=sys.stderr)
        return 1

    for row in rows:
        print(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
    print(f"PUBLICATION SYNC PASS: appended {len(rows)} publication row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
