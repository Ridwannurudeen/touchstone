"""Record the ninety-second film as numbered silent clips.

The script is `docs/FILM-SCRIPT.md`; this executes it. Six scenes: three from the live public
site, three from evidence panels rendered by `scripts/build_film_panels.py`.

**It captures a page, never a desktop.** A browser is launched from a fresh profile with no
extensions, no bookmark bar and no devtools, and only its viewport is recorded. Nothing in
frame can therefore be a filesystem path, another tab, a terminal, a wallet prompt or an
unrelated window — not because the operator remembered, but because none of them exist inside
the context being recorded.

Nothing here signs, publishes, or changes any state. It reads a public website and three local
HTML files. The film is silent by design: narration is recorded by the owner afterwards, over
picture lock.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WIDTH, HEIGHT = 1920, 1080
SITE = "https://touchstone.gudman.xyz"


@dataclass(frozen=True)
class Scene:
    number: int
    name: str
    target: str
    seconds: float
    scroll_to: int = 0
    # Held on screen long enough to read, because a viewer who cannot finish the sentence
    # under a value has not been shown the value.
    settle: float = 1.2


# Seven beats totalling ninety seconds. Scene 5 exists because the first cut did not have
# it: without the interval on screen, scene 4's rule reads as the reason this run refused,
# and it was not — the run never reached a row comparison at all.
# Eight beats, ninety seconds, and the arc the earlier cuts could not have: the system
# refuses a provisional value, and confirms the same value once the evidence earns it.
SCENES = (
    Scene(1, "verdict", f"{SITE}/", 10.0),
    Scene(2, "captures", "panel-2-captures.html", 12.0),
    Scene(3, "diff", "panel-3-diff.html", 14.0),
    Scene(4, "interval", "panel-5-interval.html", 14.0),
    Scene(5, "confirmed", "panel-6-confirmed.html", 16.0),
    Scene(6, "judge", f"{SITE}/judge", 12.0, scroll_to=900),
    Scene(7, "verify", f"{SITE}/verify", 7.0, scroll_to=520),
    Scene(8, "status", f"{SITE}/status", 5.0),
)


def _trim(source: Path, target: Path, *, start: float, length: float) -> None:
    """Drop the measured lead-in and hold exactly ``length`` seconds.

    Re-encoded rather than stream-copied: a copy can only cut at a keyframe, so the trim
    would land wherever the last keyframe happened to be and the scene would still open on
    part of the blank load.
    """
    finished = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(source),
            "-t", f"{length:.3f}",
            "-c:v", "libvpx", "-b:v", "3M", "-an",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        print(finished.stderr[-1200:], file=sys.stderr)
        raise SystemExit(f"could not trim {source.name}")
    source.unlink(missing_ok=True)


def record(scenes: tuple[Scene, ...], panels: Path, out: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with sync_playwright() as engine:
        browser = engine.chromium.launch(
            args=[
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--disable-extensions",
            ]
        )
        for scene in scenes:
            raw = out / f"raw-{scene.number}"
            raw.mkdir(parents=True, exist_ok=True)
            context = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                record_video_dir=str(raw),
                record_video_size={"width": WIDTH, "height": HEIGHT},
                # A film is not a place to discover that a certificate expired, but it is
                # also not a place to bypass one. Left at the default on purpose.
                ignore_https_errors=False,
            )
            page = context.new_page()
            url = (
                scene.target
                if scene.target.startswith("http")
                else (panels / scene.target).resolve().as_uri()
            )
            # Playwright begins recording when the CONTEXT is created, not when the page
            # finishes loading, so every clip opens on however many seconds of blank white
            # the navigation took. The first cut of this film opened on four seconds of
            # nothing and ran 108s instead of 90s for exactly that reason. So the lead-in is
            # measured rather than guessed at, and trimmed off precisely.
            started = time.monotonic()
            page.goto(url, wait_until="networkidle", timeout=60_000)
            if scene.scroll_to:
                page.mouse.wheel(0, scene.scroll_to)
            page.wait_for_timeout(int(scene.settle * 1000))
            lead_in = time.monotonic() - started
            page.wait_for_timeout(int(scene.seconds * 1000))
            context.close()

            produced = sorted(raw.glob("*.webm"))
            if not produced:
                raise SystemExit(f"scene {scene.number} produced no video")
            target = out / f"{scene.number:02d}-{scene.name}.webm"
            _trim(produced[0], target, start=lead_in, length=scene.seconds)
            shutil.rmtree(raw, ignore_errors=True)
            written.append(target)
            print(
                f"  scene {scene.number}  {scene.name:<10} {scene.seconds:>5.1f}s  {target.name}"
            )

        browser.close()
    return written


def assemble(clips: list[Path], out: Path) -> Path | None:
    """Concatenate to one silent cut, if ffmpeg is available."""
    if shutil.which("ffmpeg") is None:
        print("\nffmpeg not found; clips are written but not joined")
        return None
    listing = out / "scenes.txt"
    listing.write_text(
        "".join(f"file '{clip.name}'\n" for clip in clips), encoding="utf-8"
    )
    target = out / "touchstone-90s-silent.mp4"
    finished = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            # Re-encoded rather than stream-copied: the clips are VP8 in WebM and the
            # delivery format is H.264, and a concat demuxer copy across that boundary
            # produces a file that plays in some players and not others.
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-an",
            str(target),
        ],
        cwd=out,
        capture_output=True,
        text=True,
    )
    if finished.returncode != 0:
        print(finished.stderr[-1500:], file=sys.stderr)
        raise SystemExit("ffmpeg failed to assemble the cut")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panels", required=True, help="directory of rendered panels")
    parser.add_argument(
        "--out", required=True, help="directory for the clips and the cut"
    )
    parser.add_argument(
        "--scene",
        type=int,
        default=None,
        help="record only this scene number, for re-shooting one beat",
    )
    arguments = parser.parse_args(argv)

    panels = Path(arguments.panels)
    missing = [
        s.target
        for s in SCENES
        if not s.target.startswith("http") and not (panels / s.target).exists()
    ]
    if missing:
        raise SystemExit(
            "missing panels: "
            + ", ".join(missing)
            + " — run scripts/build_film_panels.py first"
        )

    chosen = (
        tuple(s for s in SCENES if s.number == arguments.scene)
        if arguments.scene
        else SCENES
    )
    if not chosen:
        raise SystemExit(f"no scene numbered {arguments.scene}")

    out = Path(arguments.out)
    print(f"recording {len(chosen)} scene(s) at {WIDTH}x{HEIGHT}, silent:")
    clips = record(chosen, panels, out)

    total = sum(s.seconds for s in chosen)
    print(f"\n{len(clips)} clips, {total:.0f}s of picture")
    if arguments.scene is None:
        cut = assemble(clips, out)
        if cut:
            print(f"silent cut: {cut}")
            print("Record the voiceover over this, per docs/FILM-SCRIPT.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
