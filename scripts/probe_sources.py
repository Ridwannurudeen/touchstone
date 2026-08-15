"""Probe the portfolio's source manifests read-only and report what each returns.

This is a diagnostic, not part of the surveillance path. It stores nothing, evaluates
nothing, and never writes evidence. It exists so a source's reachability can be checked
from a given host — notably the production host, where none of the portfolio has been
verified yet — without running an epoch.

Only URLs declared in `manifests/sources/*.json` are ever requested, the manifest's own
byte cap bounds every read, and no credential is read from the environment or written to
output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener


ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from touchstone.quantities import finite_positive  # noqa: E402 - after the path insert
from touchstone.sources import _NoRedirectHandler  # noqa: E402 - shared redirect policy


MANIFEST_DIR = ROOT / "manifests" / "sources"
DEFAULT_TIMEOUT = 20.0
FALLBACK_MAX_BYTES = 1_048_576
ABSOLUTE_MAX_BYTES = 8_388_608
USER_AGENT = "touchstone-probe/0.1.0 (source reachability check; nraheemst@gmail.com)"


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """One GET-able URL taken verbatim from a manifest."""

    manifest: str
    source_id: str
    url: str
    max_bytes: int
    expected_mime: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    target: ProbeTarget
    status: str
    detail: str
    byte_size: int | None
    content_type: str | None


def _redacted(url: str) -> str:
    """Drop the query string before anything is printed.

    A manifest URL can carry its credential in the query — USDY's rotating `rlkey` does
    exactly that — so the probe never emits one.
    """
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _checked_url(url: object, source_id: str) -> str:
    """Refuse anything that is not a plain HTTPS URL, whatever the manifest says."""
    if not isinstance(url, str):
        raise ValueError(f"{source_id}: url must be a string")
    split = urlsplit(url)
    if split.scheme != "https" or not split.hostname:
        raise ValueError(f"{source_id}: only https URLs may be probed")
    if split.username or split.password:
        raise ValueError(f"{source_id}: url must not carry credentials")
    if split.port not in (None, 443):
        raise ValueError(f"{source_id}: only port 443 may be probed")
    return url


def _checked_cap(value: object, source_id: str) -> int:
    """Bound the read regardless of what the manifest declares."""
    cap = FALLBACK_MAX_BYTES if value is None else value
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError(f"{source_id}: max_bytes must be a positive integer")
    if cap > ABSOLUTE_MAX_BYTES:
        raise ValueError(
            f"{source_id}: max_bytes {cap} exceeds the probe ceiling {ABSOLUTE_MAX_BYTES}"
        )
    return cap


def load_targets(manifest_dir: Path = MANIFEST_DIR) -> list[ProbeTarget]:
    """Collect every declared GET target; POST sources are reported, never sent."""
    targets: list[ProbeTarget] = []
    for path in sorted(manifest_dir.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for source in manifest.get("sources", []):
            if source.get("method") != "GET":
                continue
            url = source.get("url") or source.get("url_observed")
            if not url:
                continue
            source_id = source["source_id"]
            targets.append(
                ProbeTarget(
                    manifest=path.stem,
                    source_id=source_id,
                    url=_checked_url(url, source_id),
                    max_bytes=_checked_cap(source.get("max_bytes"), source_id),
                    expected_mime=source.get("expected_mime"),
                )
            )
    return targets


def probe(target: ProbeTarget, *, timeout: float = DEFAULT_TIMEOUT) -> ProbeResult:
    """Issue one bounded GET and describe the response without interpreting it.

    The URL and cap are re-checked here rather than trusted from the target, and the target
    must match a declared one **exactly** — same URL, same cap, same identity. Membership by
    URL alone was not enough: a hand-built target could reuse a declared URL while widening
    its byte cap, and the read would have been bounded by the forged value.
    """
    # Refused before the socket and before anything else, because it is a configuration
    # error: urlopen turns NaN or infinity into a read that never returns, and a prober
    # that hangs reports nothing at all.
    timeout = finite_positive(timeout, "timeout")
    url = _checked_url(target.url, target.source_id)
    max_bytes = _checked_cap(target.max_bytes, target.source_id)
    if target not in load_targets():
        raise ValueError(
            f"{target.source_id}: target is not exactly as declared in a manifest"
        )
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "User-Agent": USER_AGENT,
    }
    request = Request(url, method="GET", headers=headers)
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            oversize = len(body) > max_bytes
            content_type = response.headers.get("Content-Type")
            return ProbeResult(
                target=target,
                status="oversize" if oversize else "ok",
                detail=(
                    f"exceeds the manifest cap of {max_bytes} bytes"
                    if oversize
                    else f"HTTP {response.getcode()}"
                ),
                byte_size=len(body),
                content_type=content_type,
            )
    except HTTPError as error:
        return ProbeResult(target, "http_error", f"HTTP {error.code}", None, None)
    except (URLError, TimeoutError, OSError) as error:
        return ProbeResult(target, "transport_error", str(error), None, None)


def _report(results: list[ProbeResult]) -> dict[str, object]:
    return {
        "probed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "results": [
            {
                "byte_size": result.byte_size,
                "content_type": result.content_type,
                "detail": result.detail,
                "expected_mime": result.target.expected_mime,
                "manifest": result.target.manifest,
                "source_id": result.target.source_id,
                "status": result.status,
                "url": _redacted(result.target.url),
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        help="probe only this manifest stem, for example ustb",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    targets = load_targets()
    if args.manifest:
        targets = [t for t in targets if t.manifest == args.manifest]
    if not targets:
        print("no matching GET targets in manifests/sources/", file=sys.stderr)
        return 1

    results = [probe(target, timeout=args.timeout) for target in targets]
    print(json.dumps(_report(results), indent=2, sort_keys=True))
    return 0 if all(r.status == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
