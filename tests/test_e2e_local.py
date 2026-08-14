import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from web3 import Web3

from scripts.e2e_local import _start_node, run_managed_e2e


class _ForeignRpcHandler(BaseHTTPRequestHandler):
    """A non-Hardhat JSON-RPC server, which `is_connected()` alone would accept."""

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request.get("id", 1), "result": "foreign/1.0"}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.mark.local_e2e
def test_complete_loop_against_a_managed_local_hardhat_node() -> None:
    """The loop starts and stops its own clock-pinned node, so it never self-skips."""
    result = run_managed_e2e()

    assert result["asset_gate_initial"] == "allowed"
    assert result["asset_gate_after_age"] == "observation too old"
    assert result["historical_sequence"] == 1
    assert result["log_entries"] == 2
    assert result["first_transaction"] != result["correction_transaction"]


@pytest.mark.local_e2e
def test_startup_refuses_a_port_held_by_another_json_rpc_server() -> None:
    """A stolen port must fail the run, never hand it someone else's chain."""
    server = HTTPServer(("127.0.0.1", 0), _ForeignRpcHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}")).is_connected()

        with pytest.raises(RuntimeError):
            _start_node(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
