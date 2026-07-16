# Troubleshooting

Every entry here is a failure that actually happened, not a hypothetical.

---

## ffmpeg / rendering

### `No such filter: 'subtitles'` — or the filter string fails to parse

ffmpeg was built without libass. It is on PATH and works fine for everything
else, which is what makes this confusing.

Check:
```
ffmpeg -hide_banner -filters | grep subtitles
```
Empty means no libass.

**macOS is the problem child.** Homebrew's ffmpeg formula dropped libass as a
dependency, so `brew install ffmpeg` gives you a binary that cannot burn
subtitles, and `brew reinstall ffmpeg` will not help. Fixes:

- **Static build** (fast): grab one from evermeet.cx. Note it is x86_64 and
  runs under Rosetta on Apple Silicon — it still reaches VideoToolbox and even
  software x264 runs at ~7x realtime, so this is not the bottleneck you would
  expect.
- **Compile**: `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass` (20-30 min)
- **Sidestep**: use `--mode soft`. Needs no libass.

Windows (`winget install Gyan.FFmpeg`) and most Linux distro builds already
include libass.

### Subtitles are enormous and cover the frame

libass renders SRT against a **384x288 reference canvas** and scales to the real
frame. At 1080p that is a **3.75x** multiplier: `FontSize=23` lands at ~86 px.

Use `FontSize=11` (~41 px at 1080p). Above ~14 starts covering content. Test on
one frame before encoding.

### Subtitle didn't render at all

Usually you sampled a moment with no cue on screen. Check the SRT for a
timestamp that is actually covered:

```
head -20 target.srt
```

Note `-ss` before `-i` resets output timestamps to zero, so the `subtitles`
filter renders from SRT time 0 — not from your seek point. Add `-copyts` to
keep the original timeline when probing a specific moment.

### Two subtitles on screen at once

The source has captions **burned into the pixels**. Common in social and
conference video. They cannot be removed — only covered or avoided.

Raise `MarginV` to sit above them (26 usually clears a bottom-edge caption; 70
clears a taller banner). Check per section: framing changes within one video,
and a value that works for five sections can land directly on a banner in the
sixth.

### Accented characters render as boxes

Font missing or lacking those glyphs. `burn.py` picks per OS:

| OS | Font |
|---|---|
| macOS | Helvetica |
| Windows | Arial (**Helvetica does not exist on Windows**) |
| Linux | DejaVu Sans (**Arial usually does not exist**) |

Override with `--font`. Verify by rendering a frame with the tricky characters
and looking at it.

### Windows: the filter chokes on the path

`C:\videos\sub.srt` breaks the filter parser — the colon reads as an argument
separator, the backslashes as escapes. The classic workaround is
`C\:/videos/sub.srt`, but the required escaping depth varies by shell and
version.

`burn.py` avoids the class of bug entirely: it copies the subtitle into a temp
dir as `sub.srt` and runs ffmpeg with `cwd` set there, so the filter only ever
sees a bare ASCII filename. If you are writing your own ffmpeg call, do the
same.

---

## Cutting

### Cuts land seconds off, subtitles desync

`-c copy` can only cut on keyframes; ffmpeg snaps to the nearest one, up to a
full GOP away (commonly 3 s).

Two options:
- **Re-encode** for frame accuracy (costs time and a generation of quality)
- **Snap the cut points to keyframes yourself** and shift the SRT to match —
  lossless, instant, exact. This is what `srt_tools.py split` does.

Inspect the GOP first:
```
python3 scripts/srt_tools.py keyframes video.mp4
```

Snap **backwards**. Cutting forward clips the first syllable of the section;
cutting back only adds a second or two of overlap.

---

## Transcription

### Product names consistently wrong

Whisper mangles proper nouns and jargon, and does it *consistently* — one real
transcript had "Claude" as "Cloud" 110 times, "Vertex"→"Vortex",
"Vite"→"beat", "vibe coding"→"by coding", "precedence"→"presidents".

Always grep for recurring names before translating. A wrong one is wrong
hundreds of times, and the translation inherits every instance.

### Fixing the names broke other words

Naive replacement causes its own damage. Two real ones:

1. `Cloud`→`Claude` case-insensitively also destroys **Google Cloud**.
2. Protecting `G Cloud` with a case-insensitive pattern matched the tail of
   "askin**g Cloud**" → "askingcloud". Same for using/telling/giving/letting.

Protect true positives first, then replace, then restore. **Then grep to
verify** — do not assume:

```
grep -oE '\w*Cloud\w*' fixed.srt | sort | uniq -c
```

### Cue count looks doubled

`grep -c '^[0-9]*$'` matches blank lines too (`*` allows zero digits). Count
timestamps instead:

```
grep -c ' --> ' file.srt
```

---

## Translation

### Agent says OK but the file is wrong

Self-reports are not verification. Always:

```
python3 scripts/srt_tools.py validate source.srt target.srt --expect-chars '...'
```

Catches shifted timestamps, dropped/merged cues, untranslated stretches, and
markdown fences.

### Terminology drifts between chunks

No shared style guide. Write one first and hand the same file to every agent.
See `references/style-guide-template.md`.

### Turkish (and other verb-final languages) fight the cue boundaries

Verb-final syntax resists English cue splits. Redistribute meaning *across*
cues, but never change cue **count** or **timing** — those are load-bearing.

---

## Performance

### The machine is unusable during encoding

You ran several ffmpeg jobs at once. One already saturates every core (~790%
CPU on an 11-core M3 Pro). Concurrent burns buy nothing and lock up the
machine.

**Encode serially.** Parallelise LLM work instead — translation chunks, frame
analysis — which is where concurrency actually pays.

### Encoding is slower than expected

- `-preset medium` is a reasonable quality/time point; `veryfast` roughly
  halves the time at some quality cost.
- Hardware encoders (`h264_videotoolbox`, `h264_nvenc`) are much faster but
  worse per bit. Fine for a quick pass, not for archival.
- Reference: a 15-minute 1080p section took ~4 min at `libx264 -preset medium
  -crf 20` on an M3 Pro.

### Disk fills mid-encode

Check first. A 61-minute 1080p job needs several GB across source, cut sections,
and burned outputs. `burn.py` compares output duration to source, which catches
a truncated file, but free space is cheaper to check up front.
