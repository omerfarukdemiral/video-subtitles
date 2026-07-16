#!/usr/bin/env python3
"""Check that every tool this skill needs is present and actually capable.

Exits 0 if the pipeline can run end-to-end, 1 otherwise. Prints per-OS fix
instructions for whatever is missing.

The important check is libass: ffmpeg being on PATH does NOT mean it can burn
subtitles. Homebrew's ffmpeg formula dropped libass, so a stock `brew install
ffmpeg` on macOS produces a binary with no `subtitles` filter at all. Discovering
that after a 30-minute encode is the failure this script exists to prevent.
"""
import json
import os
import platform
import shutil
import subprocess
import sys

OS = platform.system()  # Darwin | Windows | Linux
IS_MAC, IS_WIN = OS == "Darwin", OS == "Windows"


def run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 1, ""


def find(name):
    return shutil.which(name)


# ── individual checks ────────────────────────────────────────────────────────

def probe_ffmpeg(exe):
    """-> (usable, has_libass)"""
    rc, out = run([exe, "-hide_banner", "-filters"])
    if not out:
        return False, False
    return True, any(len(l.split()) > 1 and l.split()[1] == "subtitles"
                     for l in out.splitlines())


def check_ffmpeg():
    """ffmpeg must exist AND expose the libass-backed `subtitles` filter.

    $VSUB_FFMPEG wins over PATH, so someone can keep their system ffmpeg and
    still burn using a second, libass-enabled build.
    """
    override = os.environ.get("VSUB_FFMPEG")
    if override:
        usable, libass = probe_ffmpeg(override)
        if not usable:
            return False, f"$VSUB_FFMPEG points at {override!r}, which will not run", \
                   "Fix the path, or unset VSUB_FFMPEG to fall back to PATH."
        if not libass:
            return False, (
                f"$VSUB_FFMPEG ({override}) has no `subtitles` filter "
                "(built without libass)"
            ), fix_ffmpeg()
        return True, f"ffmpeg with libass, via $VSUB_FFMPEG ({override})", None

    exe = find("ffmpeg")
    if not exe:
        return False, "ffmpeg not found", fix_ffmpeg()

    usable, libass = probe_ffmpeg(exe)
    if not usable:
        return False, f"ffmpeg at {exe} will not run", fix_ffmpeg()
    if not libass:
        return False, (
            f"ffmpeg at {exe} has NO `subtitles` filter (built without libass).\n"
            "     Soft subtitles (--mode soft) still work; burning does not."
        ), fix_ffmpeg(no_libass=True)

    return True, f"ffmpeg with libass ({exe})", None


def fix_ffmpeg(no_libass=False):
    if IS_MAC:
        return (
            "Homebrew's ffmpeg is built WITHOUT libass, so `brew install ffmpeg` "
            "will not fix this. Pick one:\n"
            "  a) Static build (fast, no compiling) -- keeps your system ffmpeg:\n"
            "       curl -L -o ffmpeg.zip https://evermeet.cx/ffmpeg/ffmpeg-release.zip\n"
            "       unzip ffmpeg.zip && chmod +x ffmpeg\n"
            "       export VSUB_FFMPEG=\"$PWD/ffmpeg\"\n"
            "     This build is x86_64 and runs under Rosetta on Apple Silicon.\n"
            "     It still reaches VideoToolbox, and even software x264 runs at\n"
            "     roughly 7x realtime -- not the bottleneck you would expect.\n"
            "  b) Compile with libass (~20-30 min):\n"
            "       brew tap homebrew-ffmpeg/ffmpeg\n"
            "       brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass\n"
            "  c) Skip burning and use soft subtitles (--mode soft), which need\n"
            "     no libass at all."
        )
    if IS_WIN:
        return (
            "Install a full build (these ship with libass):\n"
            "  winget install Gyan.FFmpeg\n"
            "  # or: choco install ffmpeg-full\n"
            "  # or: scoop install ffmpeg\n"
            "Then reopen the terminal so PATH refreshes."
        )
    return (
        "Install a full build:\n"
        "  sudo apt install ffmpeg          # Debian/Ubuntu\n"
        "  sudo dnf install ffmpeg          # Fedora (RPM Fusion)\n"
        "Distro builds normally include libass already."
    )


def check_ffprobe():
    exe = find("ffprobe")
    if not exe:
        return False, "ffprobe not found (ships with ffmpeg)", fix_ffmpeg()
    return True, f"ffprobe ({exe})", None


def check_ytdlp():
    exe = find("yt-dlp")
    if not exe:
        fix = ("  pip install -U yt-dlp\n"
               "  # macOS:   brew install yt-dlp\n"
               "  # Windows: winget install yt-dlp.yt-dlp")
        return False, "yt-dlp not found (only needed for URLs, not local files)", fix
    rc, out = run([exe, "--version"])
    return True, f"yt-dlp {out.strip().splitlines()[0] if out.strip() else ''} ({exe})", None


def check_whisper():
    """Any one of three backends is enough. Report which are usable."""
    found = []

    if find("whisper-cli"):
        found.append(("whisper.cpp", find("whisper-cli")))
    try:
        import faster_whisper  # noqa: F401
        found.append(("faster-whisper", "python module"))
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401
        found.append(("openai-whisper", "python module"))
    except ImportError:
        pass

    if not found:
        fix = (
            "Install ONE of these:\n"
            "  a) faster-whisper - works the same everywhere, easiest cross-platform:\n"
            "       pip install faster-whisper\n"
            "  b) whisper.cpp - fastest on Apple Silicon (Metal):\n"
            "       macOS:   brew install whisper-cpp\n"
            "       Windows: scoop install whisper-cpp   (or build from source)\n"
            "     Then fetch a model (~1.6 GB):\n"
            "       curl -L -o ggml-large-v3-turbo.bin \\\n"
            "         https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin\n"
            "  c) openai-whisper - simplest but pulls in ~2 GB of torch:\n"
            "       pip install -U openai-whisper"
        )
        return False, "no Whisper backend found", fix

    return True, "whisper: " + ", ".join(f"{n} ({w})" for n, w in found), None


def check_font():
    """Pick a font that exists on this OS and covers non-ASCII glyphs.

    Helvetica does not exist on Windows; Arial does not exist on most Linux
    boxes. Getting this wrong renders every accented character as a box.
    """
    prefer = {
        "Darwin": ["Helvetica", "Arial", "Verdana"],
        "Windows": ["Arial", "Segoe UI", "Tahoma", "Verdana"],
        "Linux": ["DejaVu Sans", "Liberation Sans", "Noto Sans"],
    }.get(OS, ["Arial"])
    return True, f"font: {prefer[0]} (fallbacks: {', '.join(prefer[1:])})", None


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 8)
    return ok, f"python {v.major}.{v.minor}.{v.micro}", (
        None if ok else "Python 3.8+ required."
    )


# ── main ─────────────────────────────────────────────────────────────────────

CHECKS = [
    ("python", check_python, True),
    ("ffmpeg", check_ffmpeg, True),
    ("ffprobe", check_ffprobe, True),
    ("yt-dlp", check_ytdlp, False),   # optional: local files need no download
    ("whisper", check_whisper, True),
    ("font", check_font, True),
]


def main():
    as_json = "--json" in sys.argv
    results, hard_fail, soft_fail = {}, [], []

    for name, fn, required in CHECKS:
        ok, msg, fix = fn()
        results[name] = {"ok": ok, "detail": msg, "fix": fix}
        if not ok:
            (hard_fail if required else soft_fail).append((name, msg, fix))

    if as_json:
        print(json.dumps({"os": OS, "ok": not hard_fail, "checks": results},
                         indent=2))
        return 0 if not hard_fail else 1

    print(f"\n  Environment check - {OS} ({platform.machine()})\n")
    for name, fn, required in CHECKS:
        r = results[name]
        mark = "OK  " if r["ok"] else ("FAIL" if required else "WARN")
        print(f"  [{mark}] {r['detail']}")

    for group, label in ((hard_fail, "MISSING"), (soft_fail, "OPTIONAL")):
        for name, msg, fix in group:
            print(f"\n  --- {label}: {name} ---\n  {msg}")
            if fix:
                print("\n  Fix:")
                for line in fix.splitlines():
                    print(f"    {line}")

    print()
    if hard_fail:
        print("  Not ready. Install what is marked FAIL above.\n")
        return 1
    print("  Ready.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
