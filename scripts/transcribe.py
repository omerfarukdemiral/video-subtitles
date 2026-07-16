#!/usr/bin/env python3
"""Transcribe audio to SRT using whichever Whisper backend is installed.

Backend order (first one found wins, override with --backend):
  whisper.cpp     fastest on Apple Silicon (Metal); needs a .bin model file
  faster-whisper  same behaviour on every OS; easiest cross-platform install
  openai-whisper  simplest, but drags in ~2 GB of torch

Always transcribe in the SOURCE language and translate afterwards. Whisper's own
--task translate only goes to English and it is markedly worse than transcribing
then translating with a real model: it flattens terminology and drops nuance.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

MODEL_DIRS = [
    os.path.expanduser("~/.whisper-models"),
    os.path.expanduser("~/whisper-models"),
    ".",
]


def find_ggml_model(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for d in MODEL_DIRS:
        hits = sorted(glob.glob(os.path.join(d, "ggml-*.bin")))
        if hits:
            # Prefer large-v3-turbo: near-large quality, a fraction of the time.
            turbo = [h for h in hits if "turbo" in h]
            return turbo[0] if turbo else hits[-1]
    return None


def detect_backend():
    if shutil.which("whisper-cli") and find_ggml_model():
        return "whisper.cpp"
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401
        return "openai-whisper"
    except ImportError:
        pass
    return None


def run_whisper_cpp(wav, lang, out_base, model, threads):
    model = find_ggml_model(model)
    if not model:
        print("  ERROR: no ggml-*.bin model found. Fetch one:\n"
              "    curl -L -o ~/.whisper-models/ggml-large-v3-turbo.bin \\\n"
              "      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
              "ggml-large-v3-turbo.bin", file=sys.stderr)
        return None
    print(f"  whisper.cpp, model {os.path.basename(model)}")
    cmd = ["whisper-cli", "-m", model, "-f", wav, "-osrt", "-of", out_base, "-pp"]
    if lang:
        cmd += ["-l", lang]
    if threads:
        cmd += ["-t", str(threads)]
    if subprocess.run(cmd).returncode != 0:
        return None
    return out_base + ".srt"


def _fmt(x):
    h = int(x // 3600); m = int(x % 3600 // 60); s = int(x % 60)
    ms = int(round((x - int(x)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def run_faster_whisper(wav, lang, out_base, model_size):
    from faster_whisper import WhisperModel
    size = model_size or "large-v3"
    print(f"  faster-whisper, model {size}")
    model = WhisperModel(size, device="auto", compute_type="auto")
    segments, info = model.transcribe(wav, language=lang, vad_filter=True)
    print(f"  detected language: {info.language} "
          f"(confidence {info.language_probability:.2f})")

    path = out_base + ".srt"
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{_fmt(seg.start)} --> {_fmt(seg.end)}\n"
                    f"{seg.text.strip()}\n\n")
            if i % 50 == 0:
                print(f"    {i} cues...", flush=True)
    return path


def run_openai_whisper(wav, lang, out_base, model_size):
    import whisper
    size = model_size or "medium"
    print(f"  openai-whisper, model {size}")
    model = whisper.load_model(size)
    r = model.transcribe(wav, language=lang, verbose=False)

    path = out_base + ".srt"
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(r["segments"], 1):
            f.write(f"{i}\n{_fmt(seg['start'])} --> {_fmt(seg['end'])}\n"
                    f"{seg['text'].strip()}\n\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="Transcribe audio to SRT.")
    ap.add_argument("audio", help="16 kHz mono WAV (see fetch.py --audio)")
    ap.add_argument("-o", "--out-base", required=True,
                    help="output path without the .srt extension")
    ap.add_argument("-l", "--lang", default=None,
                    help="source language code, e.g. en. Omit to auto-detect.")
    ap.add_argument("--backend",
                    choices=["whisper.cpp", "faster-whisper", "openai-whisper"])
    ap.add_argument("--model", default=None,
                    help="ggml .bin path (whisper.cpp) or model size name")
    ap.add_argument("--threads", type=int, default=None)
    a = ap.parse_args()

    if not os.path.exists(a.audio):
        print(f"  ERROR: not found: {a.audio}", file=sys.stderr)
        return 1

    backend = a.backend or detect_backend()
    if not backend:
        print("  ERROR: no Whisper backend. Run doctor.py.", file=sys.stderr)
        return 1

    if backend == "whisper.cpp":
        srt = run_whisper_cpp(a.audio, a.lang, a.out_base, a.model, a.threads)
    elif backend == "faster-whisper":
        srt = run_faster_whisper(a.audio, a.lang, a.out_base, a.model)
    else:
        srt = run_openai_whisper(a.audio, a.lang, a.out_base, a.model)

    if not srt or not os.path.exists(srt):
        print("  ERROR: transcription produced no SRT.", file=sys.stderr)
        return 1

    cues = open(srt, encoding="utf-8-sig").read().count(" --> ")
    print(f"\n  wrote {srt}  ({cues} cues)")
    print("\n  NEXT: proofread before translating. Whisper reliably mangles\n"
          "  proper nouns and product names, and every such error gets carried\n"
          "  into the translation. Skim the transcript for names that recur --\n"
          "  a wrong one will be wrong hundreds of times. See SKILL.md step 4.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
