"""Prove the Policy Terminal's attestation binding refuses what it must refuse.

The 2026-08-20 review found the verify panel validating a bundle's Registry v2
attestation in isolation: a genuine attestation spliced into a fabricated bundle
rendered every checkmark, including "stored on-chain report". The fix binds the
attestation to the bundle's own report and refuses chain reads addressed to any
contract but the configured registry. This script is the reproduction recipe for
that fix — it rebuilds the three adversarial fixtures from a retained public
bundle plus live chain data, drives the real page in a real browser, and asserts
the verdicts. It is manual-run by design: CI has no chain access and no browser,
and a mocked chain would prove a different claim than the one that matters.

Run from the repository root:  python scripts/check_terminal_verify.py
Needs: network access to X Layer mainnet, and Playwright with chromium installed.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Sequence 2 of the nav-settlement policy key is immutable history: the registry
# refuses rewrites, so this read is stable however far the key's sequence moves.
NAV_BUNDLE = (
    ROOT / "site2/data/eip155-196-ustb-2026-08-20-2-policy-nav-settlement-1.json"
)
OTHER_BUNDLE = (
    ROOT / "site2/data/eip155-196-ustb-2026-08-20-2-policy-disclosure-freshness-1.json"
)
MANIFEST = ROOT / "deployments/xlayer-mainnet-v2.json"
NAV_KEY = "3cd89b1b73d40adc47c25a1fbf2123dcf6839c92a446275db8f9268a146b62b8"
SEQUENCE = 2
# Any deployed contract that is not the registry. This one is real (a mainnet
# GuardedAction), which makes the refusal the interesting kind: the page must
# refuse to ask it even though it exists and answers calls.
STRANGER = "0x8FbcFf50bf1F88cADEc9103a57c4C86e8A44BAcB"

GET_REPORT_ABI = [
    {
        "name": "getReport",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "assetKey", "type": "bytes32"},
            {"name": "sequence", "type": "uint64"},
        ],
        "outputs": [
            {
                "type": "tuple",
                "components": [
                    {"name": "reportDigest", "type": "bytes32"},
                    {"name": "policyId", "type": "bytes32"},
                    {"name": "policyRoot", "type": "bytes32"},
                    {"name": "controlSetRoot", "type": "bytes32"},
                    {"name": "evidenceRoot", "type": "bytes32"},
                    {"name": "approvalDigest", "type": "bytes32"},
                    {"name": "epochKey", "type": "bytes32"},
                    {"name": "status", "type": "uint8"},
                    {"name": "observedAt", "type": "uint64"},
                    {"name": "validUntil", "type": "uint64"},
                    {"name": "publisher", "type": "address"},
                    {"name": "sequence", "type": "uint64"},
                    {"name": "parentDigest", "type": "bytes32"},
                    {"name": "reportURI", "type": "string"},
                ],
            }
        ],
    }
]


def stored_report(manifest: dict) -> tuple:
    from web3 import Web3

    error: Exception | None = None
    for rpc in (manifest["rpc_url"], "https://xlayerrpc.okx.com"):
        try:
            web3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
            registry = web3.eth.contract(
                address=Web3.to_checksum_address(manifest["registry_address"]),
                abi=GET_REPORT_ABI,
            )
            return registry.functions.getReport(bytes.fromhex(NAV_KEY), SEQUENCE).call()
        except Exception as failure:  # noqa: BLE001 - endpoint failures roll to the next
            error = failure
    raise SystemExit(f"no mainnet endpoint answered getReport: {error}")


def attestation(report: tuple, *, verifying_contract: str) -> dict:
    return {
        "chain_id": 196,
        "verifying_contract": verifying_contract,
        "asset_key": NAV_KEY,
        "report_digest": report[0].hex(),
        "policy_id": report[1].hex(),
        "policy_root": report[2].hex(),
        "control_set_root": report[3].hex(),
        "evidence_root": report[4].hex(),
        "approval_digest": report[5].hex(),
        "epoch_key": report[6].hex(),
        "status": report[7],
        "observed_at": report[8],
        "valid_until": report[9],
        "publisher": report[10],
        "sequence": report[11],
        "parent_digest": report[12].hex(),
        "correction_of": 0,
        "report_uri": report[13],
        # Deliberately not a signature. The attestation check must fail on it, and
        # everything this script asserts must hold anyway — the binding and the
        # registry pin are independent of signature validity.
        "signature": "11" * 65,
    }


def write_fixture(directory: Path, name: str, base: Path, attested: dict) -> Path:
    bundle = json.loads(base.read_text(encoding="utf-8"))
    bundle["registry_v2_attestation"] = attested
    path = directory / name
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = stored_report(manifest)
    registry = manifest["registry_address"]

    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        cases = [
            (
                "matched",
                write_fixture(
                    directory,
                    "matched.json",
                    NAV_BUNDLE,
                    attestation(report, verifying_contract=registry),
                ),
                [
                    "✓ attestation ↔ report binding",
                    "✗ registry v2 attestation",
                    "✓ stored on-chain report",
                ],
            ),
            (
                "splice",
                write_fixture(
                    directory,
                    "splice.json",
                    OTHER_BUNDLE,
                    attestation(report, verifying_contract=registry),
                ),
                [
                    "✗ attestation ↔ report binding",
                    "vouches for nothing here",
                    "· stored on-chain report",
                    "not asked",
                ],
            ),
            (
                "stranger",
                write_fixture(
                    directory,
                    "stranger.json",
                    NAV_BUNDLE,
                    attestation(report, verifying_contract=STRANGER),
                ),
                [
                    "✓ attestation ↔ report binding",
                    "✗ stored on-chain report",
                    "refusing to ask a stranger",
                ],
            ),
        ]

        port = free_port()
        server = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=ROOT / "site2",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(1.5)
            from playwright.sync_api import sync_playwright

            failures: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                console_errors: list[str] = []
                page.on(
                    "console",
                    lambda message: (
                        console_errors.append(message.text)
                        if message.type == "error"
                        else None
                    ),
                )
                page.goto(
                    f"http://127.0.0.1:{port}/app.html",
                    wait_until="networkidle",
                    timeout=60_000,
                )
                page.wait_for_timeout(2_500)
                for name, fixture, expectations in cases:
                    page.set_input_files("#verify-file", str(fixture))
                    page.wait_for_timeout(9_000)
                    text = page.inner_text("#verify-panel")
                    for expected in expectations:
                        if expected in text:
                            print(f"PASS {name}: {expected!r}")
                        else:
                            failures.append(f"{name}: missing {expected!r}")
                browser.close()
            if console_errors:
                failures.append(f"console errors: {console_errors}")
            for failure in failures:
                print(f"FAIL {failure}", file=sys.stderr)
            return 1 if failures else 0
        finally:
            server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
