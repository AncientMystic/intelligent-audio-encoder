# intelligent-audio-encoder

GPU-accelerated (CUDA / DirectML) batch audiobook transcoder — Python + FFmpeg + PySide6.

**Goal:** lossy→lossy transcoding (e.g. 32kbps mono MP3 22.05kHz → 24kbps m4b) with *no new* hiss / tinny / clicks, at batch scale. 1:1 file mapping, preserve ALL tags/chapters/cover, optional enrichment.

> **Honest GPU note:** there is no CUDA/DirectML AAC encoder. Encode is CPU via pipe: GPL FFmpeg (decode+filters) -> `exhale` (mono xHE-AAC) / `fdkaac` (stereo HE + LC) / native `aac` fallback. GPU (Nvidia via CUDA, AMD or Arc via DirectML) accelerates spectral probe + validation QA, not encode. GUI labels this honestly.
>
> **No nonfree FFmpeg build needed:** BtbN `nonfree` (libfdk_aac inside ffmpeg) is undistributable. We use standalone `tools/bin/exhale.exe` 1.2.2 + `fdkaac.exe` 1.0.5 via `core/encoder_pipe.py` — legal, clean, verified 100x+ realtime.

## Reference sample

9h28m, 130 MiB, 32kbps CBR mono MP3, 22.05kHz, MPEG v2 Layer3.
Target: 24kbps HE-AAC mono 22.05kHz m4b ≈ 97.5 MiB (−32.5 MiB, −25%).

Key rule for this class: keep native SR, lowpass to true cutoff (~9–10.5kHz), mono HE-v1 (never HEv2 for mono), SBR-off fallback to LC if validation flags hiss.

## How it works

Every file walks the same pipeline, one job per file (no splitting — a single-file book stays one file, a 20-chapter folder stays 20 files):

```
probe  →  classify  →  advise  →  encode pipe  →  preserve tags  →  validate
```

1. **Probe** (`core/probe.py`) — `ffprobe` reads codec, bitrate, sample rate, channels, duration, tags, chapters. Then ~30s of audio is decoded and FFT-analyzed to measure the *true* spectral cutoff (where the source's real frequency content ends, typically far below Nyquist on low-bitrate MP3s), the noise floor, and loudness/peak stats.
2. **Classify** (`core/probe.py:classify_content`) — the file is scored as `tts_monotone`, `natural_voice`, or `full_cast` (see below). This decides how aggressively the bitrate can drop.
3. **Advise** (`core/advisor.py`) — a rule table maps probe + content class + your chosen mode to an exact recipe: target bitrate, sample rate, channels, encoder, filter cutoffs. Manual kbps or %‑of‑source overrides skip the table.
4. **Encode pipe** (`core/encoder_pipe.py`) — FFmpeg decodes and applies only the needed filters (highpass/lowpass, light denoise *only* if the floor warrants it), then hands a temp WAV to the standalone encoder: `exhale` for mono HE/xHE, `fdkaac` for stereo HE and all LC, native `aac` as fallback. Output is `.m4b` with faststart.
5. **Preserve** (`core/tags.py`) — all metadata, chapters, and cover art are copied (`ffmpeg -map_metadata/-map_chapters` plus a `mutagen` second pass for atoms FFmpeg drops). Folder art priority: `embedded > folder.jpg > cover.jpg/png`. An `enrich` step can look up the book (OpenLibrary/Google Books/Audnexus) and fetch a cover.
6. **Validate** (`core/validator.py`) — 1–100% of the output (your slider) is decoded and compared against the source for new hiss, tinny shift, clicks, and dropouts. FAIL auto-retries with SBR off and +8k bitrate instead of silently keeping a bad file.

Batch mode runs this per file across a worker pool (default 10–12 on an 8c/16t box): either **in-place** (atomic replace with `.bak`) or **mirror-tree** into a destination folder preserving the source directory structure. A SQLite queue tracks queued/running/done/fail/retry with pause/resume.

## Modes — what quality gets applied

The Mode box picks the strategy; the control next to it always matches (`Bitrate` only for manual, `Percent` only for %, auto-hint text otherwise):

| Mode | Meaning | Typical targets (with HE tools present) |
|---|---|---|
| `transparent` | Best quality:size trade — transparent for the content class | TTS → ~21k xHE, 32k mono → 24k xHE, 128k stereo → 80k HE, 640k lossless → 96k LC |
| `maximum_saving` | Smallest safe file — pushes each class to its floor | TTS → ~18k xHE, 32k mono → 24k xHE, 128k stereo → 80k HE, 640k lossless → 96k LC |
| `archive` | Quality-first — higher floors, stereo always preserved | TTS → 28k xHE, 32k mono → 32k LC, 128k stereo → 112k LC, 640k lossless → 128k LC |
| `manual kbps` | Every file at exactly the Bitrate value (16–320 kbps) | You decide — QA still guards the result |
| `% of source` | Every file at Percent of its *probed* bitrate (10–150%) | e.g. 75% of a 32k file ≈ 24k |

Without HE tools installed the low end is compensated upward (~+30%, floored so LC never goes below 24k — thin 16k LC falls apart), and the hint tells you to install `exhale` for true HE savings. Nothing ever upscales sample rate or channels, and `full_cast` never downmixes to mono.

## Codec and player compatibility

| Codec option | What it is | File size | Plays everywhere? | QA |
|---|---|---|---|---|
| `xHE-AAC (smallest files)` | exhale USAC/xHE-AAC — best quality per bit at mono speech rates | Smallest (~24k mono) | Modern players only (Android 9+, recent iOS) — verify your shelf app first | Structural only: no CLI decoder on this system can decode xHE-AAC to PCM (ffmpeg: "Not yet implemented"; faad 2.10.1: USAC parse failure), so QA checks duration/container/bitrate, not spectrum |
| `HE-AAC (compatible)` | fdkaac HE-AAC — standard SBR, decodable by ffmpeg and virtually all players | Small | Yes | Full spectral QA |
| `AAC-LC (max compatibility)` | fdkaac/native LC — plain AAC, plays on everything including old hardware | Larger (needs ~32k+ for thin mono) | Yes | Full spectral QA |
| `MP3` | libmp3lame — for shelves that only take MP3 | Largest | Yes | Full spectral QA |

Notes: fdkaac emits stereo from mono sources in HE modes (dual-mono container) — the batch log flags `channels 1->2` when it happens. A future premium option is qaac + Apple Application Support (true mono HE-AAC, fully compatible), not yet wired.

## Why transcoding adds hiss, tinny sound, and other artifacts — and how this avoids them

Lossy→lossy conversion ("tandem coding") is inherently hostile, and low-bitrate MP3 sources like 32kbps mono are the worst case. Here is exactly what goes wrong in naive converters, and what this project does about each cause:

- **Quantization noise gets re-encoded as signal.** MP3 works by MDCT-transforming audio into frequency lines, discarding what its psychoacoustic model deems inaudible, and coarsely quantizing the rest. At 32kbps the quantization is brutal: ringing around transients ("pre-echo"), warbling "birdies" on tonal voice, and a noise floor baked into the decoded PCM. A second encoder can't tell artifact from audio, so it spends bits faithfully preserving the *damage*. **Mitigation:** we never chase bitrates below what the measured cutoff and content class support, keep the native sample rate so no new spectrum is invented to fill, and lowpass at `cutoff + 500Hz` so the encoder isn't paid to reproduce ultrasonics that are pure artifact.
- **SBR invents highs from garbage (the hiss/tinny machine).** HE-AAC's Spectral Band Replication rebuilds the top octave by transposing the low band upward guided by a small envelope side-channel. Feed it a clean master and it works; feed it an MP3 whose top band is quantization mush and it transposes that mush into bright, metallic, "tinny" highs with a constant airy hiss. **Mitigation:** SBR is used cautiously — mono low-bitrate sources get tightly band-limited HE with the crossover matched to the *measured* cutoff, and if validation flags a centroid shift (>8% = tinny) or high-band energy rise (+3dB = hiss), the file auto-retries as plain LC with SBR off and +8k bitrate.
- **Parametric Stereo on mono wastes bits and smears.** HE-AACv2's PS tool parameterizes the stereo image — meaningless on mono narration, and it can phase-smear what little is there. **Mitigation:** mono never uses HEv2, period. Stereo is preserved only for `full_cast`.
- **Upsampling fabricates an empty band.** Resampling 22.05kHz → 44.1kHz creates an 11–22kHz band containing nothing but interpolated quantization noise; the encoder then either hisses it or wastes bits on it. **Mitigation:** the sample rate is *never* raised. TTS above 22050Hz is the sole exception allowed downward (voice needs nothing above ~8kHz).
- **Level normalization lifts the noise floor.** Loudness-matching a noisy source turns up the hiss between words along with the voice. **Mitigation:** no blanket normalization; a light `afftdn` denoise is applied only when the measured silence floor is worse than −50dB, and `loudnorm` targets audiobook levels (I=-19, TP=-2) without chasing peak gain.
- **Clicks, pops, clipping, dropouts.** MP3 frame damage, lossy-decode overshoots (inter-sample peaks), and I/O glitches under parallel load cause transients the ear catches instantly. **Mitigation:** transient/dropout detectors in validation (spike and RMS-dip analysis), conditional `adeclick` only when clicks are actually detected, atomic `.bak` replacement so a crashed job can never leave a truncated book, and gapless-aware muxing (`--moov-before-mdat`, proper encoder delay signaling).

## How content detection works (TTS vs natural voice vs full cast)

Bitrate needs differ wildly: flat robotic TTS is nearly a single modulated tone and survives ~18k; a single narrator needs formant detail (~24k HE at 32k-source quality); a full-cast drama with music and effects needs stereo width and transient response (80–128k). The classifier (`analyze_content` + `classify_content`) measures this instead of guessing from the filename:

- **`cutoff_hz`** — averaged 4096-point magnitude spectrum over voiced (non-silent) audio; passband reference is the median of 500Hz–6kHz; the cutoff is the highest bin staying above `max(−25dB, noise_floor + 10dB)` with a 3-bin sustain check (brickwall guard), with a 95%-energy rolloff fallback. A 32k MP3 typically measures ~8–9.5kHz despite a 11025Hz Nyquist.
- **`dyn_range_db`** — standard deviation of per-second RMS levels in dB, silence excluded. Robotic TTS sits almost flat (<3.5dB); a narrator breathes and emphasizes (~4–7dB); drama with music/SFX swings wide (>7.5dB).
- **`hf_ratio`** — fraction of spectral power above 6kHz. Speech is low (<0.05–0.12); music, effects, and applause push it up.
- **`silence_pct` / `crest`** — pause structure and peak-to-average ratio, supporting evidence for narration vs produced audio.
- **Container facts** — channels, sample rate, and source bitrate from `ffprobe` (stereo + ≥96k strongly suggests produced audio; ≤48k mono suggests narration).

Decision rules: stereo with high rate/dynamics/HF (or any source ≥128k stereo) → `full_cast`; very flat dynamics on low-bitrate mono → `tts_monotone`; everything else → `natural_voice` (the safe default — misclassifying TTS as natural only costs some bytes, never quality). A manual content override is honored whenever set, and the chosen class is recorded in the recipe (`reason` string) so batch logs show *why* each file got its bitrate.

## Quickstart (Windows)

```powershell
# 1. FFmpeg GPL + standalone HE encoders (legal, no nonfree build)
.\tools\fetch_ffmpeg.ps1
.\tools\fetch_tools.ps1
ffmpeg -encoders | Select-String aac
tools\bin\exhale\exhale.exe -h
tools\bin\fdkaac\fdkaac.exe --help

# 2. App (CPU-only light path)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py

# 3. With GPU analysis (RTX 2060 CUDA / Arc DirectML)
pip install -r requirements-gpu.txt
python app.py --backend cuda --gpu 0

# Quick launchers (Windows / Linux-macOS)
start_gui.bat
./start_gui.sh
```

`python app.py --list-gpus` shows every detected device; `--backend/--gpu` (or the GUI's Backend/GPU boxes, which persist to `gpu_selection.json`) pick the compute device for probe + QA.

## Layout

```
app.py
core/devices.py   # CPU/CUDA/DirectML + dual-GPU enum (RTX 2060 + Arc A310)
core/probe.py     # ffprobe + spectral cutoff/noise/speech detect
core/advisor.py   # auto-bitrate rules (no-skip, 32k→24k etc.)
core/encoder.py   # ffmpeg cmd builder (native aac/mp3 only) + check_ffmpeg incl tools/bin
core/encoder_pipe.py # PIPE: ffmpeg -> WAV -> exhale(mono HE) / fdkaac(stereo HE+LC) -> m4b
core/tags.py      # copy ALL tags/chapters/cover, folder-art, diff check
core/enrich.py    # OpenLibrary/GoogleBooks/Audnexus lookup + cover fetch
core/queue.py     # SQLite job DB + worker pool (file-level parallel)
core/validator.py # 1-100% QA: hiss/tinny/click metrics + auto-retry
gui/main_window.py
tools/fetch_ffmpeg.ps1
tests/
```

## License

MIT — see LICENSE. FFmpeg binaries are NOT bundled; user provides them.
