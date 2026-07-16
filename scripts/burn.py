#!/usr/bin/env python3
"""Burn a subtitle file into the picture, or mux it as a soft subtitle track.

Two modes:
  hard  - render the text into the pixels. Needed for X/Twitter, Instagram,
          TikTok, and anywhere else that ignores subtitle tracks. Re-encodes.
  soft  - attach the .srt as a selectable track. Instant, lossless, toggleable,
          and can carry several languages at once. Ignored by social platforms.

Two portability traps this file exists to handle:

1. Path escaping. libavfilter parses the filter string itself, so a Windows path
   like C:\\v\\s.srt is read as a filter argument separator (the colon) plus
   escape sequences (the backslashes). The usual advice is to write C\\:/v/s.srt,
   but the escaping depth varies by shell and ffmpeg version and it is genuinely
   fiddly. This script sidesteps the whole class of bug: it copies the subtitle
   to a plain ASCII filename in a working directory and runs ffmpeg with cwd set
   there, so the filter only ever sees `sub.srt`. No colons, no backslashes, no
   spaces, no non-ASCII.

2. Font scaling. libass renders an SRT against a 384x288 reference canvas and
   scales the result to the real frame. At 1080p that multiplies every size by
   3.75, so FontSize=23 lands at ~86 px and swallows the frame. Sizes here are
   in the 288-px reference space; 11 is a sane default and ~41 px on 1080p.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile

OS = platform.system()

FONTS = {
    "Darwin": "Helvetica",
    "Windows": "Arial",       # Helvetica does not exist on Windows
    "Linux": "DejaVu Sans",   # Arial usually does not exist on Linux
}
DEFAULT_FONT = FONTS.get(OS, "Arial")

# The ffmpeg on PATH is frequently not the one that can burn subtitles --
# Homebrew's is built without libass. Rather than force people to replace their
# system ffmpeg, let them point at a second binary just for this.
#   export VSUB_FFMPEG=/path/to/ffmpeg-with-libass
# ffprobe is read separately and from PATH: probing needs no libass, and static
# ffmpeg downloads often ship without an ffprobe beside them.
DEFAULT_FFMPEG = os.environ.get("VSUB_FFMPEG", "ffmpeg")
DEFAULT_FFPROBE = os.environ.get("VSUB_FFPROBE", "ffprobe")


def ffprobe_duration(path, ffprobe=DEFAULT_FFPROBE):
    p = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except (ValueError, AttributeError):
        return None


def has_libass(ffmpeg):
    """A missing `subtitles` filter is the single most common cause of a failed
    burn, and the raw ffmpeg error ('Error parsing filterchain') names neither
    libass nor the fix. Check up front and say something useful."""
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=25)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None  # binary itself is unusable
    out = (p.stdout or "") + (p.stderr or "")
    return any(len(l.split()) > 1 and l.split()[1] == "subtitles"
               for l in out.splitlines())


def build_style(font, size, margin_v, margin_lr, outline, primary, outline_col):
    """force_style string. Sizes are in libass's 288-px reference space."""
    return ",".join([
        f"FontName={font}",
        f"FontSize={size}",
        "Bold=1",
        f"PrimaryColour={primary}",
        f"OutlineColour={outline_col}",
        "BorderStyle=1",
        f"Outline={outline}",
        "Shadow=0",
        f"MarginV={margin_v}",
        f"MarginL={margin_lr}",
        f"MarginR={margin_lr}",
    ])


def burn_hard(video, srt, out, args):
    """Re-encode with the subtitles rendered in. Runs ffmpeg from a temp cwd so
    the filter never sees a path it has to parse."""
    work = tempfile.mkdtemp(prefix="vsub_")
    try:
        # Plain ASCII names inside the working dir -> nothing for libavfilter
        # to choke on, on any OS.
        shutil.copy2(srt, os.path.join(work, "sub.srt"))

        style = build_style(args.font, args.font_size, args.margin_v,
                            args.margin_lr, args.outline,
                            args.primary_colour, args.outline_colour)
        vf = f"subtitles=sub.srt:force_style='{style}'"

        cmd = [args.ffmpeg, "-y", "-v", "error", "-stats",
               "-i", os.path.abspath(video),
               "-vf", vf,
               "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", args.vcodec, "-pix_fmt", "yuv420p"]

        if args.vcodec == "libx264":
            cmd += ["-preset", args.preset, "-crf", str(args.crf)]
        else:  # hardware encoders take a bitrate, not a CRF
            cmd += ["-b:v", args.bitrate]

        cmd += ["-c:a", "copy", "-movflags", "+faststart",
                os.path.abspath(out)]

        print(f"  encoding ({args.vcodec})... this is the slow part", flush=True)
        p = subprocess.run(cmd, cwd=work)
        return p.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def mux_soft(video, subs, out, langs, ffmpeg=DEFAULT_FFMPEG):
    """Attach subtitle tracks without touching the video. Instant and lossless.
    Needs no libass, so the stock ffmpeg is fine here."""
    cmd = [ffmpeg, "-y", "-v", "error", "-i", os.path.abspath(video)]
    for s in subs:
        cmd += ["-i", os.path.abspath(s)]

    cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    for i in range(len(subs)):
        cmd += ["-map", str(i + 1)]

    cmd += ["-c", "copy", "-c:s", "mov_text"]
    for i, lang in enumerate(langs):
        cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
    if subs:
        cmd += ["-disposition:s:0", "default"]

    cmd += ["-movflags", "+faststart", os.path.abspath(out)]
    return subprocess.run(cmd).returncode


def main():
    ap = argparse.ArgumentParser(
        description="Burn or mux subtitles into a video.")
    ap.add_argument("video")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--mode", choices=["hard", "soft"], default="hard",
                    help="hard: render into pixels (needed for X/Instagram). "
                         "soft: selectable track (instant, lossless).")
    ap.add_argument("--srt", action="append", required=True,
                    help="Subtitle file. Repeat for several soft tracks; "
                         "hard mode burns only the first.")
    ap.add_argument("--lang", action="append", default=[],
                    help="ISO-639-2 code per --srt, e.g. tur, eng.")

    # Style. Sizes are in libass's 288-px reference space -- see module docstring.
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--font-size", type=int, default=11,
                    help="288-px reference space. 11 is ~41 px at 1080p. "
                         "Above ~14 starts covering the frame.")
    ap.add_argument("--margin-v", type=int, default=26,
                    help="Bottom margin. Raise it if the source already has "
                         "captions burned in near the bottom.")
    ap.add_argument("--margin-lr", type=int, default=70)
    ap.add_argument("--outline", type=float, default=1.4)
    ap.add_argument("--primary-colour", default="&H00FFFFFF")   # white
    ap.add_argument("--outline-colour", default="&H00000000")   # black

    # Encoding
    ap.add_argument("--vcodec", default="libx264",
                    choices=["libx264", "h264_videotoolbox", "h264_nvenc",
                             "h264_qsv", "h264_amf"],
                    help="libx264 is the portable default. Hardware encoders "
                         "are faster but lower quality per bit.")
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--bitrate", default="6M",
                    help="Used only by hardware encoders, which ignore --crf.")

    # Binaries
    ap.add_argument("--ffmpeg", default=DEFAULT_FFMPEG,
                    help="ffmpeg to use. Point this at a libass-enabled build "
                         "when the system one lacks it (common on macOS via "
                         "Homebrew). Also settable as $VSUB_FFMPEG.")
    ap.add_argument("--ffprobe", default=DEFAULT_FFPROBE,
                    help="ffprobe to use. Needs no libass, so the system one is "
                         "normally fine. Also settable as $VSUB_FFPROBE.")

    args = ap.parse_args()

    for f in [args.video] + args.srt:
        if not os.path.exists(f):
            print(f"  ERROR: not found: {f}", file=sys.stderr)
            return 1

    # Fail early and legibly. ffmpeg's own message for a missing libass is
    # "Error parsing filterchain", which points at neither the cause nor the fix.
    if args.mode == "hard":
        libass = has_libass(args.ffmpeg)
        if libass is None:
            print(f"  ERROR: cannot run ffmpeg at {args.ffmpeg!r}.",
                  file=sys.stderr)
            return 1
        if not libass:
            print(f"  ERROR: {args.ffmpeg} was built without libass, so it has "
                  "no `subtitles` filter and cannot burn.\n"
                  "  Options:\n"
                  "    - point at a libass build:  --ffmpeg /path/to/ffmpeg\n"
                  "      (or: export VSUB_FFMPEG=/path/to/ffmpeg)\n"
                  "    - use --mode soft, which needs no libass\n"
                  "    - run doctor.py for per-OS install instructions",
                  file=sys.stderr)
            return 1

    src_dur = ffprobe_duration(args.video, args.ffprobe)

    if args.mode == "hard":
        if len(args.srt) > 1:
            print("  NOTE: hard mode burns only the first --srt "
                  f"({os.path.basename(args.srt[0])}); the rest are ignored.")
        rc = burn_hard(args.video, args.srt[0], args.out, args)
    else:
        langs = args.lang or ["und"] * len(args.srt)
        if len(langs) != len(args.srt):
            print("  ERROR: give one --lang per --srt.", file=sys.stderr)
            return 1
        rc = mux_soft(args.video, args.srt, args.out, langs, args.ffmpeg)

    if rc != 0:
        print("  ffmpeg failed.", file=sys.stderr)
        return rc

    # Verify rather than trust: a zero exit code with a truncated file is a
    # real failure mode when a disk fills mid-encode.
    if not os.path.exists(args.out) or os.path.getsize(args.out) == 0:
        print("  ERROR: output missing or empty.", file=sys.stderr)
        return 1

    out_dur = ffprobe_duration(args.out, args.ffprobe)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\n  wrote {args.out}  ({size_mb:.0f} MB, {out_dur:.1f}s)")

    if src_dur and out_dur and abs(src_dur - out_dur) > 1.0:
        print(f"  WARNING: duration drifted {src_dur:.1f}s -> {out_dur:.1f}s. "
              "Output may be truncated.")
        return 1

    if args.mode == "hard":
        print("  Check a frame before trusting it -- see SKILL.md step 6.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
