# video-subtitles

A [Claude Code](https://claude.com/claude-code) skill that adds translated
subtitles to any video — burned into the picture, or as a selectable track.

Give it an X/Twitter link, a YouTube URL, or a local file. It downloads,
transcribes with Whisper, translates with Claude, and renders. Works on
macOS, Windows, and Linux.

```
you: add Turkish subtitles to https://x.com/user/status/123
```

## Why not just use an auto-caption tool

Auto-captioning is fine until the video is technical. Machine translation
mangles jargon that your audience already knows in English: a Turkish developer
says "tool call", not "araç çağrısı". This skill translates with a model, guided
by a style guide *you* pin down — which terms stay in English, which get
translated, which are product names that must never change.

It also knows the failure modes, because they all happened while building it:

- **`ffmpeg` on PATH doesn't mean it can burn subtitles.** Homebrew's ffmpeg
  ships without libass, so `brew install ffmpeg` gives you a binary with no
  `subtitles` filter. `doctor.py` catches this in two seconds instead of after
  a 30-minute encode.
- **libass scales SRT against a 288px canvas.** At 1080p that's a 3.75x
  multiplier, so `FontSize=23` renders at ~86px and swallows the frame. Use 11.
- **Whisper mangles proper nouns consistently.** One transcript came back with
  "Claude" as "Cloud" 110 times. Every instance would have shipped in the
  translation.
- **Many videos already have captions burned into the pixels.** They can't be
  removed — your subtitle has to sit above them.
- **`-c copy` only cuts on keyframes.** Cuts land up to a GOP off and desync the
  subtitles, unless you snap the cut points yourself.

## Install

As a plugin — this also gets you updates via `/plugin marketplace update`:

```
/plugin marketplace add omerfarukdemiral/video-subtitles
/plugin install video-subtitles@ofd-plugins
```

Or clone it straight into your skills directory:

```bash
git clone https://github.com/omerfarukdemiral/video-subtitles.git \
  ~/.claude/skills/video-subtitles
```

Either way, check your environment before the first run:

```bash
python3 ~/.claude/skills/video-subtitles/scripts/doctor.py
```

`doctor.py` tells you what's missing and how to fix it **on your OS**. Nothing
is installed without your say-so.

### Requirements

| | Purpose | Install |
|---|---|---|
| Python 3.8+ | scripts | preinstalled on macOS/Linux |
| ffmpeg **with libass** | rendering | see below — the fiddly one |
| yt-dlp | URLs only | `pip install -U yt-dlp` |
| Whisper backend | transcription | any of three, see below |

**ffmpeg + libass.** The trap. `ffmpeg -hide_banner -filters \| grep subtitles`
must print something.

- **macOS** — Homebrew's build has no libass and reinstalling won't help. Either
  grab a static build from [evermeet.cx](https://evermeet.cx/ffmpeg/) and point
  `VSUB_FFMPEG` at it, or compile:
  `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass`
- **Windows** — `winget install Gyan.FFmpeg` already includes libass.
- **Linux** — distro builds normally include it.

You don't have to replace your system ffmpeg. Point at a second one:

```bash
export VSUB_FFMPEG=/path/to/ffmpeg-with-libass
```

**Whisper** — pick one:

| Backend | Install | Notes |
|---|---|---|
| faster-whisper | `pip install faster-whisper` | easiest cross-platform |
| whisper.cpp | `brew install whisper-cpp` + a [model](https://huggingface.co/ggerganov/whisper.cpp) | fastest on Apple Silicon |
| openai-whisper | `pip install -U openai-whisper` | simple, pulls ~2GB of torch |

## Platform status

Be honest about what's been run and where.

| OS | Status |
|---|---|
| **macOS (Apple Silicon)** | **Verified.** Built and tested here end to end. |
| **Windows** | **Written, not tested.** Cross-platform by construction — the two known traps (path escaping, font names) are handled — but nobody has run it yet. Reports welcome. |
| **Linux** | **Written, not tested.** Expected to be the easiest of the three: distro ffmpeg ships with libass. |

The Windows path trap is worth explaining, since it's the usual reason ffmpeg
subtitle commands break there. `C:\videos\sub.srt` breaks libavfilter's parser —
the colon reads as an argument separator, the backslashes as escapes. The usual
advice is to write `C\:/videos/sub.srt`, but the required escaping depth varies
by shell and version. This skill sidesteps the whole class of bug: it copies the
subtitle to a temp dir as `sub.srt` and runs ffmpeg with `cwd` set there, so the
filter only ever sees a bare ASCII filename. Same code on every OS.

## Usage

Normally you just ask Claude Code and it follows `SKILL.md`. The scripts also
stand alone:

```bash
S=~/.claude/skills/video-subtitles/scripts

python3 $S/doctor.py

python3 $S/fetch.py "https://x.com/user/status/123" --outdir work --audio
python3 $S/transcribe.py work/audio16k.wav -o work/source -l en

# translate work/source.srt -> work/target.srt, then:
python3 $S/srt_tools.py validate work/source.srt work/target.srt \
  --expect-chars 'çğışöüÇĞİŞÖÜ'

# burn it in (for X, Instagram, TikTok)
python3 $S/burn.py work/video.mp4 -o out.mp4 --mode hard --srt work/target.srt

# or attach both languages as switchable tracks (instant, lossless)
python3 $S/burn.py work/video.mp4 -o out.mp4 --mode soft \
  --srt work/target.srt --lang tur --srt work/source.srt --lang eng
```

### Burned or soft?

|  | soft | hard |
|---|---|---|
| Speed | instant | re-encodes |
| Quality | untouched | slight loss |
| Languages | several, switchable | one, permanent |
| X / Instagram / TikTok | **ignored** | works |
| Watching it yourself | better | worse |

Social platforms ignore subtitle tracks, so anything you post needs `hard`. For
your own viewing `soft` wins outright — and you can carry the source language
alongside the translation to check a line you doubt.

### Sections

Split a long video into separately shareable parts:

```bash
python3 $S/srt_tools.py split work/video.mp4 work/target.srt \
  --sections sections.json --outdir sections
```

Cuts snap **backwards** to the nearest keyframe, so the stream copy is exact and
lossless — no re-encode, no quality loss, done in seconds. Sections overlap by
the snap amount (0–3s) on purpose: cutting forward would clip the first syllable.
Section SRTs are rebased to match. See `sections.example.json`.

## How it does the translation

1. **Proofread the transcript first.** ASR errors are consistent and multiply
   into the translation.
2. **Write a style guide** (`references/style-guide-template.md`) — terminology
   drifts between chunks without one.
3. **Translate chunks in parallel subagents**, all sharing that guide.
4. **Validate** — `srt_tools.py validate` catches shifted timestamps, dropped or
   merged cues, and untranslated stretches. Agents report "OK" on files with all
   of these; check for yourself.
5. **Look at a frame** before encoding minutes of video.

Encodes run **serially**. One ffmpeg already saturates every core (~790% CPU on
an 11-core M3 Pro); running six at once buys nothing and locks up the machine.
Parallelism belongs on the LLM work, not the encoder.

## Contributing

Windows and Linux reports are the most useful thing right now — see the platform
table. Include your OS, `python3 scripts/doctor.py` output, and the failing
command.

`references/troubleshooting.md` catalogues every failure hit so far, with causes
and fixes.

## Credit where it's due

If you're subtitling someone else's video and reposting it, credit the original
creator. Translation makes a work reach further; it doesn't make it yours.

## License

MIT — see [LICENSE](LICENSE).
