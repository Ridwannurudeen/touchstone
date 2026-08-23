"""Deny Python network connections for the hermetic blocking-CI regression test."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import traceback
from xml.sax.saxutils import quoteattr


DENIAL = "HERMETIC CI DENIED NETWORK ACCESS"
_REAL_SOCKET = socket.socket


def _record_attempt(boundary: str) -> None:
    marker = os.environ.get("TOUCHSTONE_HERMETIC_ATTEMPT_FILE")
    if marker is not None:
        Path(marker).write_text(
            boundary + "\n" + "".join(traceback.format_stack()[:-1]), encoding="utf-8"
        )


class DeniedSocket(_REAL_SOCKET):
    """Remain a socket type for importers such as ssl, but refuse connections."""

    def connect(self, *args, **kwargs):
        del args, kwargs
        _record_attempt("socket.socket.connect")
        raise RuntimeError(f"{DENIAL}: socket.socket.connect")

    def connect_ex(self, *args, **kwargs):
        del args, kwargs
        _record_attempt("socket.socket.connect_ex")
        raise RuntimeError(f"{DENIAL}: socket.socket.connect_ex")


def _deny_create_connection(*args, **kwargs):
    del args, kwargs
    _record_attempt("socket.create_connection")
    raise RuntimeError(f"{DENIAL}: socket.create_connection")


def _deny_getaddrinfo(*args, **kwargs):
    del args, kwargs
    _record_attempt("socket.getaddrinfo")
    raise RuntimeError(f"{DENIAL}: socket.getaddrinfo")


socket.socket = DeniedSocket
socket.create_connection = _deny_create_connection
socket.getaddrinfo = _deny_getaddrinfo

_REAL_RUN = subprocess.run


def _run_without_nested_pytest(*popenargs, **kwargs):
    arguments = kwargs.get("args", popenargs[0] if popenargs else None)
    if not (
        os.environ.get("TOUCHSTONE_HERMETIC_MUTATION_STUBS") == "1"
        and isinstance(arguments, list)
        and arguments[1:3] == ["-m", "pytest"]
    ):
        return _REAL_RUN(*popenargs, **kwargs)

    report_argument = next(
        (
            argument
            for argument in arguments
            if isinstance(argument, str) and argument.startswith("--junit-xml=")
        ),
        None,
    )
    if report_argument is None:
        return subprocess.CompletedProcess(arguments, 0, "", "")

    tests = arguments[3 : arguments.index("-q")]
    cases = []
    for test in tests:
        path, _, function = test.partition("::")
        module = path.removesuffix(".py").replace("/", ".")
        cases.append(
            f"<testcase classname={quoteattr(module)} name={quoteattr(function)}>"
            "<failure>hermetic mutation child stub</failure></testcase>"
        )
    report = Path(report_argument.partition("=")[2])
    report.write_text(
        '<testsuites><testsuite name="pytest" errors="0" skipped="0" '
        f'failures="{len(cases)}" tests="{len(cases)}">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(arguments, 1, "", "")


# The real mutation job runs separately. Re-running its 125 child pytest processes inside the
# hermetic pytest test would duplicate that job and exceed the Python job's timeout. This
# substitution leaves mutation_check itself under the socket denial and executes its complete
# mutation/restore loop, while only replacing child verdicts that the real job proves.
subprocess.run = _run_without_nested_pytest
