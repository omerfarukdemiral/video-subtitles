#!/usr/bin/env python3
"""Fetch a video and report what it actually is before anything expensive runs.

Accepts any yt-dlp-supported URL (X/Twitter, YouTube, Vimeo, ...) or a local
file, which is passed straight through.

The `probe` output matters more than it looks. A tweet link can be a 30-second
clip or a 61-minute conference talk, and the two demand completely different
plans: number of cues to translate, encode minutes, and whether the target
platform will even accept the result. Check duration before promising anything.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys


def probe(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None

    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    subs = [s for s in d.get("streams", []) if s.get("codec_type") == "subtitle"]
    fmt = d.get("format", {})

    dur = float(fmt.get("duration", 0) or 0)
    return {
        "path": path,
        "duration_s": round(dur, 2),
        "duration_hms": f"{int(dur // 3600):02d}:{int(dur % 3600 // 60):02d}:{int(dur % 60):02d}",
        "size_mb": round(int(fmt.get("size", 0) or 0) / 1e6, 1),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": v.get("r_frame_rate"),
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name"),
        "has_audio": bool(a),
        "soft_subtitle_tracks": len(subs),
    }


def download(url, outdir, fmt):
    if not shutil.which("yt-dlp"):
        print("  ERROR: yt-dlp not installed. Run doctor.py.", file=sys.stderr)
        return None
    os.makedirs(outdir, exist_ok=True)
    tmpl = os.path.join(outdir, "source.%(ext)s")

    cmd = ["yt-dlp", "-o", tmpl, "--no-playlist"]
    if fmt:
        cmd += ["-f", fmt]
    cmd.append(url)

    print(f"  downloading: {url}")
    if subprocess.run(cmd).returncode != 0:
        print("  ERROR: yt-dlp failed.", file=sys.stderr)
        return None

    # yt-dlp merges into an unpredictable extension; take the newest non-partial.
    got = [os.path.join(outdir, f) for f in os.listdir(outdir)
           if f.startswith("source.") and not f.endswith((".part", ".ytdl"))]
    if not got:
        print("  ERROR: download produced no file.", file=sys.stderr)
        return None
    return max(got, key=os.path.getmtime)


def extract_audio(video, out):
    """16 kHz mono WAV - what every Whisper backend wants."""
    rc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out]).returncode
    return out if rc == 0 else None


def main():
    ap = argparse.ArgumentParser(description="Fetch a video and probe it.")
    ap.add_argument("target", help="URL or local file path")
    ap.add_argument("--outdir", default="vsub_work")
    ap.add_argument("--format", default=None, help="yt-dlp -f selector")
    ap.add_argument("--audio", action="store_true",
                    help="also extract 16kHz mono WAV for Whisper")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    is_url = re.match(r"^https?://", a.target, re.I)
    if is_url:
        video = download(a.target, a.outdir, a.format)
        if not video:
            return 1
    else:
        if not os.path.exists(a.target):
            print(f"  ERROR: not found: {a.target}", file=sys.stderr)
            return 1
        video = os.path.abspath(a.target)
        os.makedirs(a.outdir, exist_ok=True)

    info = probe(video)
    if not info:
        print(f"  ERROR: ffprobe could not read {video}", file=sys.stderr)
        return 1

    if a.audio:
        wav = os.path.join(a.outdir, "audio16k.wav")
        info["audio_wav"] = extract_audio(video, wav)
        if not info["audio_wav"]:
            print("  ERROR: audio extraction failed.", file=sys.stderr)
            return 1

    if a.json:
        print(json.dumps(info, indent=2))
        return 0

    print(f"\n  video:    {info['path']}")
    print(f"  duration: {info['duration_hms']}  ({info['duration_s']}s)")
    print(f"  picture:  {info['width']}x{info['height']} {info['vcodec']} @ {info['fps']}")
    print(f"  audio:    {info['acodec'] or 'NONE'}")
    print(f"  size:     {info['size_mb']} MB")
    if info["soft_subtitle_tracks"]:
        print(f"  subtitle tracks already present: {info['soft_subtitle_tracks']}")
    if a.audio:
        print(f"  wav:      {info['audio_wav']}")

    mins = info["duration_s"] / 60
    print(f"\n  Rough cost at this length (~{mins:.0f} min):")
    print(f"    transcribe  ~{max(1, mins * 0.1):.0f}-{max(2, mins * 0.3):.0f} min")
    print(f"    cues to translate  ~{mins * 15:.0f}")
    print(f"    burn (libx264)  ~{max(1, mins * 0.25):.0f} min")
    if mins > 20:
        print("\n  Long video. Confirm scope with the user before translating:\n"
              "  the whole thing, or just a section?")
    if not info["has_audio"]:
        print("\n  WARNING: no audio stream - nothing to transcribe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
