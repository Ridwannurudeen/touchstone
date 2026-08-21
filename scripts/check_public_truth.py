"""Compare repository site counts with the human-visible live status page."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "site2" / "_data" / "facts.json"
STATUS_URL = "https://touchstone.gudman.xyz/status"
MAX_STATUS_BYTES = 1_000_000

COUNT_PATTERNS = {
    "reports_published": re.compile(r"(?<!\d)(\d[\d,]*)\s+reports\b", re.I),
    "confirmed_reports": re.compile(
        r"(?<!\d)(\d[\d,]*)\s+reached\s+CONFIRMED\b", re.I
    ),
    "enforcement_txs": re.compile(
        r"(?<!\d)(\d[\d,]*)\s+(?:permit/refuse\s+)?enforcement "
        r"transactions\b",
        re.I,
    ),
}


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        del attributes
        if tag in {"script", "style"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def parse_live_counts(document: str) -> dict[str, int | None]:
    parser = VisibleTextParser()
    parser.feed(document)
    text = parser.text()
    counts: dict[str, int | None] = {}
    for key, pattern in COUNT_PATTERNS.items():
        matches = pattern.findall(text)
        if len(matches) > 1:
            raise ValueError(f"live /status contains multiple values for {key}")
        counts[key] = int(matches[0].replace(",", "")) if matches else None
    return counts


def load_fact_counts(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    counts = payload.get("counts") if isinstance(payload, dict) else None
    if not isinstance(counts, dict):
        raise ValueError("facts.json has no counts object")
    parsed: dict[str, int] = {}
    for key in COUNT_PATTERNS:
        value = counts.get(key)
        if not isinstance(value, str) or not value.replace(",", "").isdigit():
            raise ValueError(f"facts.json count {key!r} is not a digit string")
        parsed[key] = int(value.replace(",", ""))
    return parsed


def fetch_status(url: str) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "Accept-Encoding": "identity",
            "User-Agent": "Touchstone-Public-Truth/1.0",
        },
    )
    with urlopen(request, timeout=15) as response:
        body = response.read(MAX_STATUS_BYTES + 1)
        if len(body) > MAX_STATUS_BYTES:
            raise ValueError("live /status exceeds the 1000000-byte limit")
        charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset)


def differences(
    local: dict[str, int], live: dict[str, int | None]
) -> list[tuple[str, int, int | None]]:
    return [
        (key, local[key], live[key])
        for key in COUNT_PATTERNS
        if local[key] != live[key]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the live-network comparison and exit successfully",
    )
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--url", default=STATUS_URL)
    arguments = parser.parse_args(argv)

    if arguments.offline:
        print("public truth network check skipped (--offline); no live claim was verified")
        return 0

    try:
        local = load_fact_counts(arguments.facts)
        live = parse_live_counts(fetch_status(arguments.url))
        mismatches = differences(local, live)
    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        print(f"PUBLIC TRUTH FAIL: {error}", file=sys.stderr)
        print(
            "OPERATOR NOTE: live /status is host-generated; facts.json is the "
            "repository build input.",
            file=sys.stderr,
        )
        return 1

    if mismatches:
        print(
            "PUBLIC TRUTH FAIL: repository facts disagree with host-generated live "
            "/status"
        )
        for key, local_value, live_value in mismatches:
            displayed_live = "NOT EXPOSED" if live_value is None else str(live_value)
            print(
                f"  {key}: facts.json={local_value}, live /status={displayed_live}"
            )
        print(
            "OPERATOR NOTE: live /status is host-generated; facts.json is the "
            "repository build input. Regenerate the stale side intentionally."
        )
        return 1

    print("public truth matches host-generated live /status")
    print(
        "OPERATOR NOTE: live /status is host-generated; facts.json is the repository "
        "build input."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
