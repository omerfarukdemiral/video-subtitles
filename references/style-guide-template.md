# Style Guide Template

Fill this in **before** translating, and hand the same file to every agent.
Without it, chunk 3 renders a term one way and chunk 7 another, and the video
reads as though two people translated it.

Delete the guidance in brackets as you go.

---

## Audience and register

[Who is watching? A subtitle is read in 2-3 seconds, so register drives every
other choice. "Turkish developers who already work in English daily" leads
somewhere very different from "a general audience with no technical background".]

- Tone: [conversational / formal / technical]
- Sentence length: short. Long clauses do not survive on screen.
- Do not mirror source syntax. Translate the meaning, then say it the way a
  native speaker would.
- Filler ("like", "you know", "I mean", "kind of") mostly disappears. Do not
  render every one.

## Stays in the source language — do not translate

[The single highest-leverage section. If the audience already uses these terms
in English, translating them makes the subtitle *worse*: Turkish developers say
"tool call", not "araç çağrısı". List every term explicitly — an agent guessing
per-chunk is exactly how drift starts.]

```
[term, term, term, ...]
```

## Product names, people, handles — verbatim

[Never translated, never "corrected", never localised. Include spellings the
transcript got wrong so agents do not reintroduce them.]

```
[Product Name, Person Name, file.ext, /command, ...]
```

## Gets translated — settled equivalents

[Words with an equivalent the audience actually uses. One row per term so
agents cannot diverge.]

| Source | Target |
|---|---|
| file | [ ] |
| folder | [ ] |
| code base | [ ] |
| feature | [ ] |

## Worked examples

[Two or three real lines from *this* transcript, translated the way you want.
Examples pin down register far better than adjectives do — an agent reading
"conversational" still guesses; an agent reading a sample copies it.]

```
SOURCE: [line]
TARGET: [line]
```

```
SOURCE: [line]
TARGET: [line]
```

---

## Format rules — non-negotiable

1. Cue index and timestamp preserved **byte for byte**. Not one character
   changes.
2. Cue count in == cue count out. No merging, no splitting.
3. A sentence spanning two cues stays split across two cues. Verb-final
   languages (Turkish, Japanese, ...) fight this — redistribute meaning across
   the cues, but never touch cue **count** or **timing**.
4. Wrap past ~42 characters onto a second line (`\n`) inside the same cue.
5. Output raw SRT. No markdown fences, no commentary, no preamble.
