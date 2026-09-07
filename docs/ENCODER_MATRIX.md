# Encoder matrix — verified 2026-09-06 on Girl Zero (9h28m 32k mono MP3 22050, cutoff 8311Hz)

Toolchain: GPL `ffmpeg` (gyan full, native aac + libmp3lame) + standalone `tools/bin/exhale/exhale.exe` 1.2.2 + `tools/bin/fdkaac/fdkaac.exe` 1.0.5.
No nonfree FFmpeg build needed (BtbN nonfree undistributable). Legal + GitHub-clean.
Pipe: `ffmpeg decode+highpass/lowpass -> temp WAV -> exhale/fdkaac -> m4b`. Always `fdkaac -S --moov-before-mdat` (else moov missing).

| Source | Cutoff | Target | Tool | SR/ch | Filters | Result |
|---|---|---|---|---|---|---|
| 32k mono natural (Girl Zero bulk) | 8311 | 24k xHE mono m4b | exhale preset 0 | keep 22050/1 | highpass 70 + lowpass 8811 | 30s -> 97298B, 24975bps, mono xHE, 25.7k actual. Full book ~99.9MB (-23%) |
| TTS monotone flat | <9k | 18-21k xHE mono | exhale preset a/b | keep 22050/1 (downsample to 22050 if higher) | same | 30s preset a -> 58114B, 15.2k actual |
| Same mono but QA FAIL hiss | same | 32k LC mono | fdkaac -p 2 / native | keep | same, SBR OFF | Removes SBR invention of highs |
| 64k stereo natural | 10-14k | 48k xHE mono | exhale | downmix 1 | lowpass 14k | Voice-safe |
| 128k stereo full-cast music/SFX | 15k+ | 80k HE stereo | fdkaac -p 5 / exhale | preserve stereo/SR | light | 37% saving, never downmix |
| 640k FLAC/WAV full-cast | 20k | 96k LC stereo | fdkaac -p 2 | preserve | light | 85% saving |

Known quirks:
- fdkaac HE upmixes mono->stereo (verified: mono WAV -> stereo HE). Policy: mono HE always via exhale; fdkaac for stereo HE + all LC.
- exhale presets are CVBR: mono 0->~24k, a->~18k. Warning about preset 0/a quality is expected upstream text, safe for speech at these rates.
- exhale xHE-AAC needs modern player; fallback LC if compatibility required.
- Speed: fdkaac 100-118x, exhale fast, native ~30x realtime single worker.

MP3 output: libmp3lame joint-stereo only when user explicitly picks MP3.
