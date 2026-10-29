# pdf-to-musicxml

Convert PDF sheet music — printed scans, phone photos, or vector scores exported from
Sibelius / Finale / MuseScore — into editable MusicXML, plus MIDI, a re-typeset PDF,
and a proofreading report.

This is a WorkBuddy skill package: a workflow plus a command-line pipeline that actually runs.

**English** | [简体中文](README.md)

## What it solves

Paper score → editable note data. The core is optical music recognition (OMR), powered by
[Audiveris](https://github.com/Audiveris/audiveris) — the most complete open-source OMR engine,
and it bundles its own JRE so it needs neither Python packages nor a system-wide Java.

> ⚠️ **The engine is not in this repository.** Audiveris is ~163 MB and is excluded via `.gitignore`.
> On first use, run `scripts/install_engine.py` to download and unpack it, or follow
> `references/setup.md` to install it offline from a manual download.

## Accuracy (measured, not marketing)

| Metric | Result |
|---|---|
| Pitch sequence match | **97.5%** (vector score with ground truth: 53 measures / 435 notes) |
| Pitch + duration match | **96.1%** |
| Exact match on aligned portion | 98.6% |
| Clefs / key / time / grand staff | Correct |
| Repeat signs, 1st/2nd endings | Correct |
| Triplets | **Lost** (engine emits `divisions=4`, which cannot represent 1/3 beat) |
| Short system at page bottom | Frequently dropped entirely |
| Ornaments / dynamics | **False positives** (`tr`, `mf`, accents appear out of nowhere) |
| Lyrics (vector PDF) | **100% lossless** — extracted from the text layer, no OCR |
| Lyrics (scan) | Barely usable in English; **Chinese OCR is effectively unusable** |

Position it as "saves you 80% of engraving work", not "done in one click".

## Requirements

| Component | Required | Notes |
|---|---|---|
| Python 3.9+ | Yes | Script runtime |
| `pymupdf` | Yes | PDF parsing, rendering, text-layer extraction |
| `numpy` + `opencv-python-headless` | No | Only for the fallback preprocessing in `render.py` |
| Audiveris | Yes | OMR engine, installed separately |
| MuseScore 4 | Recommended | Used for MIDI / PDF export |

```bash
python -m pip install pymupdf numpy opencv-python-headless
```

## Quick start

```bash
# 0. Health check
python scripts/p2m.py doctor

# 1. Install the engine (first run: ~81 MB download + unpack)
python scripts/install_engine.py

# 2. Convert
python scripts/p2m.py run "score.pdf"

# Only the first 3 pages
python scripts/p2m.py run "score.pdf" --sheets 1-3
```

Output goes to `<name>_musicxml/` next to the PDF: MusicXML, MIDI, re-typeset PDF,
`lyrics.txt` (vector PDFs), and `report.md`.

## Scripts

| File | Responsibility |
|---|---|
| `scripts/p2m.py` | Orchestrator. `run` executes the pipeline, `doctor` checks the environment, `probe` inspects a PDF |
| `scripts/install_engine.py` | Downloads and unpacks Audiveris without touching the system; installs OCR language data |
| `scripts/extract_lyrics.py` | Lossless lyric extraction from a vector PDF text layer (replaces OCR) |
| `scripts/gh_download.py` | Chunked parallel downloader for rate-limited networks |
| `scripts/probe.py` | Classifies pages as vector or scanned, and infers native DPI |
| `scripts/render.py` | Rendering + preprocessing (denoise, autocrop); the fallback path when recognition is poor |

## Known pitfalls (all learned the hard way)

1. **`-sheets` swallows arguments.** It is a multi-valued option and will consume the input
   file path as one of its own values, raising `NumberFormatException`. Separate the input
   file with `--`.
2. **OCR defaults to `eng` only.** Installing `chi_sim` does not enable it. Set it explicitly:
   `-constant org.audiveris.omr.text.Language.defaultSpecification=eng+chi_sim`.
3. **Language data must come from `tessdata` (full version).** `tessdata_fast` / `tessdata_best`
   lack the legacy components and fail with
   `Could not initialize TessBaseAPI languages ... in legacy mode`.
4. **Normalize `divisions` before comparing accuracy.** `<duration>` is an integer in units of
   `divisions`; comparing two MusicXML files directly yields absurd results like 0.7% match.
5. **Do not expect OCR to read Chinese lyrics.** Vector PDFs work through the text layer (100%);
   for scans, transcribe by hand.

More detail in `references/setup.md` and `SKILL.md`.

## License

[MIT](LICENSE) © 2026 zyuanlbj
