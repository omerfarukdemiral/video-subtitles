---
name: video-subtitles
description: Transcribe a video and add translated subtitles in any language, either burned into the picture or as a selectable soft track. Takes an X/Twitter, YouTube, or any yt-dlp URL, or a local file. Cross-platform (macOS/Windows/Linux). Use when the user wants subtitles, captions, translation, or "add Turkish/Spanish/etc subs to this video".
---

# Video Subtitles

Pipeline: **fetch → transcribe → proofread → translate → position → render → verify**

Scripts live in `scripts/`. Run them with `python3` (or `python` on Windows).
They handle the mechanical work; you handle the two things a script cannot:
**translating well** and **looking at the result**.

## Before anything: check the environment

```
python3 scripts/doctor.py
```

Do not skip this. `ffmpeg` being on PATH does **not** mean it can burn
subtitles — Homebrew's ffmpeg ships without libass, so a stock macOS install
has no `subtitles` filter at all. `doctor.py` catches that in two seconds
instead of after a 30-minute encode. It prints the fix for the user's actual OS.

If a required tool is missing, show the user the fix and let them run it.
Do not silently install things.

**Second ffmpeg.** Nobody should have to replace their system ffmpeg to burn one
video. If the working ffmpeg lives elsewhere, point at it — every script honours
this and `doctor.py` reports through it:

```
export VSUB_FFMPEG=/path/to/ffmpeg-with-libass    # or: burn.py --ffmpeg ...
```

ffprobe is read separately from PATH: probing needs no libass, and static ffmpeg
downloads often ship with no ffprobe beside them.

---

## 1. Fetch and probe

```
python3 scripts/fetch.py "<URL or file>" --outdir work --audio
```

**Read the duration before promising anything.** A tweet link can be a 30-second
clip or a 61-minute talk. This changes everything downstream: cue count, encode
time, and whether the target platform even accepts the length (X caps at 2:20
without Premium, 4 hours with it).

If the video is longer than ~20 minutes, stop and ask what the user actually
wants: the whole thing, or one section? Give them the cost estimate `fetch.py`
prints. Do not start translating an hour of speech on an assumption.

## 2. Transcribe

```
python3 scripts/transcribe.py work/audio16k.wav -o work/source -l en
```

Transcribe in the **source** language. Do not use Whisper's `--task translate`:
it only targets English and it flattens terminology. Transcribe, then translate
properly in step 4.

## 3. Proofread the transcript — do not skip this

Whisper reliably mangles proper nouns, product names, and jargon, and it mangles
them *consistently*. Every error here gets multiplied into the translation.

Read the transcript. Then grep for whatever recurs:

```
grep -oiE '\b(claude|cloud|clod)\b' work/source.srt | sort | uniq -c | sort -rn
```

Real example: a talk about **Claude Code** came back with "Cloud" 110 times,
plus "Vertex"→"Vortex", "Vite"→"beat", "vibe coding"→"by coding". Uncaught,
every one of those ships in the translation.

**Watch out when fixing:** a naive replace does damage of its own. Replacing
`Cloud`→`Claude` case-insensitively also destroys "Google Cloud". And a
protective pattern like `G Cloud` matched case-insensitively eats the tail of
"askin**g Cloud**" → "askingcloud". Protect the true positives first, replace,
then restore — and **verify with a grep afterwards** rather than assuming:

```
grep -oE '\w*Cloud\w*' work/source_fixed.srt | sort | uniq -c
```

## 4. Translate

The translation is the whole point of the skill. Do it properly.

**Write a style guide first.** Without one, chunk 3 renders "tool call" one way
and chunk 7 another, and the video reads as though two people translated it.
See `references/style-guide-template.md`. It must pin down:

- **Register** — subtitles are read in 2-3 seconds. Short sentences. Do not
  mirror source syntax.
- **What stays in the source language.** For a technical audience, translating
  jargon that the audience already uses in English makes the subtitle *worse*.
  Turkish developers say "tool call", not "araç çağrısı". List those terms.
- **What gets translated** — the words with settled native equivalents.
- **Product names and people** — never translated, never "corrected".

**For anything over ~200 cues, split it and translate the chunks in parallel
subagents.** Give every agent the same style guide. One agent grinding through
900 cues alone is slow and drifts as it goes.

**Rules every agent must follow:**
1. Cue indices and timestamps preserved **byte for byte**.
2. Cue count in == cue count out. No merging, no splitting.
3. Output raw SRT. No markdown fences, no commentary.

Then **verify it yourself — do not trust the agents' self-reports**:

```
python3 scripts/srt_tools.py validate work/source.srt work/target.srt \
  --expect-chars 'çğışöüÇĞİŞÖÜ'
```

This catches shifted timestamps, dropped cues, merged cues, untranslated
stretches, and markdown fences. Agents cheerfully report "OK" on files with
all of these.

## 5. Choose the mode — this is a real decision, not a default

| | **soft** | **hard** |
|---|---|---|
| Speed | instant | re-encodes |
| Quality | untouched | slight loss |
| Languages | several, switchable | one, permanent |
| X, Instagram, TikTok | **ignored** | works |
| Personal viewing | better | worse |

**Ask what the video is for.** If the user wants to watch and understand it,
soft subs win on every axis — and you can ship the source language alongside
the target so they can check a line they doubt. If it is going on social media,
soft subs are invisible and hard is the only option. Nothing stops you doing
both; the soft mux costs seconds.

## 6. Position the subtitle — look at a frame first

Two traps here, both of which cost a full re-encode if you skip this step.

**Font scaling.** libass renders SRT against a 384x288 reference canvas and
scales up to the real frame. At 1080p everything is multiplied by **3.75**.
`FontSize=23` becomes ~86 px and swallows the picture. **Use 11.** Above ~14
starts covering content.

**The source may already have captions burned in.** Plenty of social and
conference video ships with hardcoded captions in the pixels. They cannot be
removed. Your subtitle will collide with them unless you sit above them —
raise `MarginV`.

So: **render one frame and actually look at it** before encoding minutes of video.

```
ffmpeg -y -v error -ss 305 -copyts -i work/video.mp4 \
  -vf "subtitles=work/target.srt:force_style='FontName=Helvetica,FontSize=11,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1.4,Shadow=0,MarginV=26,MarginL=70,MarginR=70'" \
  -frames:v 1 /tmp/probe.png
```

Then `Read` the PNG. Judge:
- Readable? Overflowing?
- Colliding with captions already in the picture?
- Covering a face, slides, terminal output?
- Do the target language's characters render, or are they boxes?

**Pick a timestamp where a cue is actually on screen** — land in a gap and you
will conclude, wrongly, that nothing rendered.

Sample **several** moments. Framing shifts within one video. Real example: one
section's defaults were fine for five sections, but a sixth had a white banner
across the bottom that `MarginV=26` landed right on top of, illegibly. Only
looking caught it; `MarginV=70` fixed it.

## 7. Render

```
python3 scripts/burn.py work/video.mp4 -o out.mp4 --mode hard \
  --srt work/target.srt --font-size 11 --margin-v 26
```

Soft, with both languages:

```
python3 scripts/burn.py work/video.mp4 -o out.mp4 --mode soft \
  --srt work/target.srt --lang tur --srt work/source.srt --lang eng
```

`burn.py` picks the right font per OS (Helvetica / Arial / DejaVu Sans) and
sidesteps Windows path escaping by running ffmpeg from a temp dir where the
subtitle is just `sub.srt`.

**Never run several burns in parallel.** One ffmpeg already saturates every
core (~790% CPU on an 11-core M3 Pro). Running six concurrently buys no
throughput and makes the machine unusable. Encode **serially**. Parallelise the
LLM work — translation, frame checks — instead; that is where concurrency pays.

## 8. Verify the output

Trust nothing. Check:
1. Output exists, non-zero, duration matches source (`burn.py` does this).
2. Render a frame from the **finished** file and `Read` it. Subtitle present?
   Characters correct? Covering nothing?

Report honestly. If something is off, say so.

---

## Optional: sections

For splitting a long video into separately shareable parts:

```
python3 scripts/srt_tools.py keyframes work/video.mp4
python3 scripts/srt_tools.py split work/video.mp4 work/target.srt \
  --sections sections.json --outdir sections
```

`sections.json`:
```json
[{"slug":"01-intro","title":"Intro","start":"00:00:00,000","end":"00:15:22,260"}]
```

Cuts snap **backwards** to the nearest keyframe, so a stream copy is exact and
lossless — no re-encode. Sections overlap by the snap amount (0-3 s), which is
deliberate: cutting forward would clip the first syllable. Section SRTs are
rebased to match automatically.

Find boundaries by reading the transcript for topic changes, then grep the
phrase to get its timestamp.

---

## Reference

- `references/troubleshooting.md` — errors, causes, fixes
- `references/style-guide-template.md` — fill in per job

## The short version

1. `doctor.py` first — ffmpeg on PATH means nothing.
2. Check duration before promising anything.
3. Proofread the transcript — ASR errors multiply into the translation.
4. Style guide before translating; parallel chunks; **validate the output**.
5. Soft vs hard is a real decision. Ask.
6. **Look at a frame** before encoding. FontSize=11, not 23.
7. Burns run serially. LLM work runs parallel.
8. Look at the finished file too.
