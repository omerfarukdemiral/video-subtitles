#!/usr/bin/env python3
"""SRT utilities: validate, shift, split, and cut a video on keyframes.

Commands
  validate SRC TRANSLATED   compare a translation against its source
  shift    SRC OUT --by N   move every timestamp by N seconds
  keyframes VIDEO           list keyframe times (cut points that stay lossless)
  split    VIDEO SRT        cut into sections, snapping to keyframes

Why `validate` matters: a translator that silently merges two cues, or drops the
tail of a long file, produces an SRT that still *looks* fine. The desync only
shows up on playback, long after the encode. This compares cue-by-cue and
insists the timestamps come back byte-identical.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def t2s(t):
    m = TS.match(t.strip())
    if not m:
        raise ValueError(f"bad timestamp: {t!r}")
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def s2t(x):
    x = max(0.0, x)
    h = int(x // 3600)
    m = int((x % 3600) // 60)
    s = int(x % 60)
    ms = int(round((x - int(x)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse(path):
    """-> [{'idx', 'start', 'end', 'text'}]. Tolerates BOM and CRLF."""
    raw = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    cues = []
    for block in raw.strip().split("\n\n"):
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        a, b = lines[1].split(" --> ")
        cues.append({
            "idx": lines[0].strip(),
            "start": a.strip(),
            "end": b.strip(),
            "text": "\n".join(lines[2:]).strip(),
        })
    return cues


def dump(cues):
    return "\n".join(f"{c['idx']}\n{c['start']} --> {c['end']}\n{c['text']}\n"
                     for c in cues)


# ── validate ────────────────────────────────────────────────────────────────

def cmd_validate(a):
    src, tr = parse(a.source), parse(a.translated)
    problems = []

    if len(src) != len(tr):
        problems.append(f"cue count {len(src)} -> {len(tr)} (must match exactly)")

    for i, (s, t) in enumerate(zip(src, tr), 1):
        if s["start"] != t["start"] or s["end"] != t["end"]:
            problems.append(
                f"cue {i}: timestamp changed\n"
                f"      source:     {s['start']} --> {s['end']}\n"
                f"      translated: {t['start']} --> {t['end']}")
            break
        if not t["text"].strip():
            problems.append(f"cue {i}: empty text")
            break

    raw = open(a.translated, encoding="utf-8-sig").read()
    if "```" in raw:
        problems.append("markdown fence in file - the model wrapped its output")

    # A handful of identical cues is normal (names, numbers, 'OK'). Many long
    # identical cues means whole stretches were never translated.
    untouched = sum(1 for s, t in zip(src, tr)
                    if s["text"].strip().lower() == t["text"].strip().lower()
                    and len(s["text"]) > 25)
    if untouched > max(3, len(src) * 0.02):
        problems.append(f"{untouched} long cues identical to source - "
                        "likely untranslated")

    if a.expect_chars:
        if not re.search(f"[{a.expect_chars}]", raw):
            problems.append(
                f"no {a.expect_chars!r} characters found - wrong language, or "
                "an encoding problem")

    if problems:
        print(f"  FAIL {os.path.basename(a.translated)}")
        for p in problems:
            print(f"    - {p}")
        return 1
    print(f"  OK   {os.path.basename(a.translated)}  ({len(tr)} cues)")
    return 0


# ── shift ───────────────────────────────────────────────────────────────────

def cmd_shift(a):
    cues = parse(a.source)
    for c in cues:
        c["start"] = s2t(t2s(c["start"]) + a.by)
        c["end"] = s2t(t2s(c["end"]) + a.by)
    open(a.out, "w", encoding="utf-8").write(dump(cues))
    print(f"  shifted {len(cues)} cues by {a.by:+.3f}s -> {a.out}")
    return 0


# ── keyframes ───────────────────────────────────────────────────────────────

def keyframe_times(video, upto=None):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-skip_frame", "nokey",
           "-show_entries", "frame=best_effort_timestamp_time",
           "-of", "csv=p=0"]
    if upto:
        cmd += ["-read_intervals", f"%+{upto}"]
    cmd.append(video)
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    times = []
    for line in out.splitlines():
        line = line.strip().rstrip(",")
        if line:
            try:
                times.append(float(line))
            except ValueError:
                pass
    return sorted(times)


def cmd_keyframes(a):
    ks = keyframe_times(a.video, upto=a.upto)
    if not ks:
        print("  no keyframes found", file=sys.stderr)
        return 1
    gaps = [round(b - x, 3) for x, b in zip(ks, ks[1:])]
    print(f"  {len(ks)} keyframes in the sampled range")
    print(f"  first: {ks[:6]}")
    if gaps:
        common = max(set(gaps), key=gaps.count)
        print(f"  typical gap: {common}s")
        print(f"  -> a stream-copy cut can land up to {common}s off unless the "
              "cut point is snapped to a keyframe")
    return 0


# ── split ───────────────────────────────────────────────────────────────────

def snap_down(t, keys):
    """Nearest keyframe at or before t. Cutting early keeps every word; cutting
    late clips the first syllable of the section."""
    prev = [k for k in keys if k <= t + 1e-6]
    return prev[-1] if prev else 0.0


def cmd_split(a):
    spec = json.load(open(a.sections, encoding="utf-8"))
    os.makedirs(a.outdir, exist_ok=True)

    keys = keyframe_times(a.video)
    if not keys:
        print("  ERROR: could not read keyframes", file=sys.stderr)
        return 1

    cues = parse(a.srt)
    bounds = [t2s(s["start"]) for s in spec] + [t2s(spec[-1]["end"])]
    snapped = [snap_down(b, keys) for b in bounds[:-1]] + [bounds[-1]]

    manifest = []
    for i, sec in enumerate(spec):
        s0_want, s0 = bounds[i], snapped[i]
        s1 = snapped[i + 1]
        delta = s0_want - s0   # cut starts this much earlier than the boundary
        slug = sec["slug"]

        # Section SRT, rebased so the cut's first frame is t=0.
        out = []
        for c in cues:
            ca, cz = t2s(c["start"]), t2s(c["end"])
            if cz <= s0_want or ca >= t2s(sec["end"]):
                continue
            na = max(ca, s0_want) - s0_want + delta
            nz = min(cz, t2s(sec["end"])) - s0_want + delta
            if nz - na < 0.05:
                continue
            out.append({"idx": str(len(out) + 1), "start": s2t(na),
                        "end": s2t(nz), "text": c["text"]})

        srt_path = os.path.join(a.outdir, f"{slug}.srt")
        open(srt_path, "w", encoding="utf-8").write(dump(out))

        vid_path = os.path.join(a.outdir, f"{slug}.mp4")
        if not a.srt_only:
            # Stream copy: instant and bit-exact, because s0 is a keyframe.
            rc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-ss", f"{s0:.3f}", "-to", f"{s1:.3f}",
                 "-i", a.video, "-c", "copy",
                 "-movflags", "+faststart", vid_path]).returncode
            if rc != 0:
                print(f"  ERROR: cut failed for {slug}", file=sys.stderr)
                return rc

        manifest.append({"slug": slug, "title": sec.get("title", slug),
                         "start": round(s0, 3), "end": round(s1, 3),
                         "duration": round(s1 - s0, 3),
                         "srt": srt_path,
                         "video": None if a.srt_only else vid_path,
                         "keyframe_shift": round(delta, 3)})
        print(f"  {slug:34s} {s2t(s1 - s0)[:8]}  {len(out):4d} cues  "
              f"(snapped back {delta:.2f}s)")

    mpath = os.path.join(a.outdir, "manifest.json")
    json.dump(manifest, open(mpath, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print(f"\n  manifest: {mpath}")
    print("  Sections overlap by the snap amount so no word is lost at a cut.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="compare a translation to its source")
    v.add_argument("source")
    v.add_argument("translated")
    v.add_argument("--expect-chars", default="",
                   help="regex char class the target language must contain, "
                        "e.g. çğışöüÇĞİŞÖÜ for Turkish")
    v.set_defaults(fn=cmd_validate)

    s = sub.add_parser("shift", help="move every timestamp")
    s.add_argument("source")
    s.add_argument("out")
    s.add_argument("--by", type=float, required=True, help="seconds, may be negative")
    s.set_defaults(fn=cmd_shift)

    k = sub.add_parser("keyframes", help="list keyframe times")
    k.add_argument("video")
    k.add_argument("--upto", type=int, default=120, help="seconds to sample")
    k.set_defaults(fn=cmd_keyframes)

    p = sub.add_parser("split", help="cut into sections on keyframes")
    p.add_argument("video")
    p.add_argument("srt")
    p.add_argument("--sections", required=True,
                   help='JSON: [{"slug","title","start":"HH:MM:SS,mmm","end":...}]')
    p.add_argument("--outdir", required=True)
    p.add_argument("--srt-only", action="store_true",
                   help="write section SRTs but do not cut the video")
    p.set_defaults(fn=cmd_split)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
