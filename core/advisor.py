"""Auto-bitrate advisor. No-skip policy: always returns a target, even for 32k sources.

Content tiers (user requirement -- wildly varying library):
  tts_monotone  -- robotic/flat TTS, needs nearly nothing -> 16-21k HE (24-28k LC fallback)
  natural_voice -- single narrator (bulk of 32k mono audiobooks) -> 24k HE (32k LC fallback)
  full_cast     -- multi-voice + music/SFX -> 64-96k HE stereo or 96-128k LC, preserve stereo/SR
Encoder reality: gyan full_build has native aac + libmp3lame, NO libfdk_aac.
  -> prefer fdk/exhale if present, else native LC with +30% bitrate compensation at <48k.
"""
from __future__ import annotations

# Generic low-bitrate mono fixture (e.g. 9.5hr 32k CBR mono MP3 audiobook @22.05kHz).
# Used by tests/demos only — real files are probed, never hardcoded.
SAMPLE_32K_MONO = {
    "codec": "mp3", "bitrate_kbps": 32.0, "sample_rate": 22050, "channels": 1,
    "duration_sec": 34116.0, "cutoff_hz": 9500, "speech_score": 0.95,
    "content": "natural_voice",
}

def _resolve_encoder(want_he: bool, avail: dict) -> tuple[str, str]:
    ch_hint = avail.get("_channels", 1)
    has_exhale = bool(avail.get("exhale"))
    has_fdkaac = bool(avail.get("fdkaac"))
    has_fdk = bool(avail.get("libfdk_aac"))
    if want_he and ch_hint == 1 and has_exhale:
        return "exhale", "he_aac"  # mono HE: exhale preserves mono; fdkaac upmixes mono->stereo
    if want_he and has_fdk:
        return "libfdk_aac", "he_aac"
    if want_he and has_exhale:
        return "exhale", "he_aac"
    if want_he and has_fdkaac:
        return "fdkaac", "he_aac"  # stereo HE only; caller avoids mono
    if has_fdk:
        return "libfdk_aac", "aac_low"
    if has_fdkaac:
        return "fdkaac", "aac_low"
    return "aac", "aac_low"  # native LC, universally available

def advise_for_sample(info: dict, mode: str = "transparent", manual_kbps: int = 0, pct: int = 0,
                     avail: dict | None = None, content: str = "auto") -> dict:
    """Returns encoder recipe. mode: transparent|maximum_saving|archive."""
    avail = avail or {}
    src_br = float(info.get("bitrate_kbps", 64))
    sr = int(info.get("sample_rate", 44100))
    ch = int(info.get("channels", 2))
    cutoff = int(info.get("cutoff_hz", 0)) or min(sr // 2, 14000)
    speech = float(info.get("speech_score", 0.8))
    ctype = content if content != "auto" else info.get("content", "auto")
    if ctype == "auto":
        # light auto when classifier not run: use br/ch/sr heuristic
        if ch >= 2 and (sr >= 32000 or src_br >= 96):
            ctype = "full_cast" if src_br >= 96 else "natural_voice"
        elif src_br <= 48 and ch == 1:
            ctype = "natural_voice"  # default bulk; TTS only when classifier says so (dyn<3.5)
        else:
            ctype = "natural_voice"

    he_available = bool(avail.get("libfdk_aac") or avail.get("exhale") or avail.get("fdkaac"))
    comp = 1.0 if he_available else 1.3  # LC needs ~30% more bits down low

    if manual_kbps:
        tgt = manual_kbps
        reason = f"manual {manual_kbps}k [{ctype}]"
        want_he = tgt <= 64 and ctype != "full_cast"
    elif pct:
        tgt = max(16, int(src_br * pct / 100))
        reason = f"{pct}% of {src_br:.0f}k [{ctype}]"
        want_he = tgt <= 64 and ctype != "full_cast"
    elif ctype == "tts_monotone":
        base = 18 if mode == "maximum_saving" else (21 if mode == "transparent" else 28)
        tgt = int(base * comp) if not he_available and base <= 32 else base
        if not he_available:
            tgt = min(max(tgt, 24), 32)  # LC floor: don't do 16k LC, it falls apart
        reason = f"TTS monotone flat -> {tgt}k {'HE' if he_available else 'LC-comp'} keep-SR (no-skip)"
        want_he = True
    elif ctype == "full_cast":
        if src_br >= 300:  # 640k-ish FLAC/WAV/high
            tgt = 96 if mode != "archive" else 128
        elif src_br >= 96:
            tgt = 80 if mode != "archive" else 112
        elif src_br <= 48:
            # lowSRC but music/SFX: don't upscale (wastes space, no quality back).
            # use 40-48k LC to minimize 2nd-gen loss without bloating.
            tgt = 48 if mode != "archive" else 56
        else:
            tgt = 64 if mode != "archive" else 96
        # hard cap: never exceed source + 16k (prevents 32k->64k bloat)
        tgt = min(tgt, int(src_br) + 16) if src_br < 96 else tgt
        reason = f"full-cast music/SFX stereo -> {tgt}k preserve-stereo/SR"
        want_he = tgt <= 80
    else:  # natural_voice — bulk of low-bitrate mono audiobooks
        if src_br <= 48 and ch == 1 and cutoff < 11000:
            base = 24 if mode != "archive" else 32
            tgt = int(base * comp) if not he_available else base
            if not he_available:
                tgt = 32  # native LC at 24k mono = thin; bump to 32k (≈ same size as src, but m4b+tags win + still 0% bigger, stable)
            reason = f"low-bitrate mono natural {src_br:.0f}k cut@{cutoff}Hz -> {tgt}k {'HE' if he_available else 'LC32-fallback (no fdk)'} keep-SR"
            want_he = True
        elif src_br <= 96 and speech > 0.6:
            tgt = 48 if he_available else 64
            reason = f"speech 64-96k -> {tgt}k"
            want_he = True
        elif src_br <= 160:
            tgt = 64
            reason = "midrate -> 64k"
            want_he = speech > 0.6
        else:
            tgt = 96 if speech > 0.6 else 128
            reason = "highrate/CD -> 96/128k LC"
            want_he = False

    avail2 = dict(avail or {})
    avail2["_channels"] = ch
    encoder, profile = _resolve_encoder(want_he, avail2)

    # channel/SR policy: TTS+natural may downmix to mono ≤64k; full_cast NEVER downmixes
    if ctype == "full_cast":
        out_ch, out_sr = ch, sr
    elif ctype == "tts_monotone" and sr > 22050:
        out_ch, out_sr = 1, 22050  # TTS needs nothing above 8k; downsample saves without harm
    else:
        out_ch = 1 if (speech > 0.7 and ch <= 2 and tgt <= 64 and ctype != "full_cast") else ch
        out_sr = sr  # never upscale; downsample only for TTS above

    return {
        "target_kbps": tgt, "sample_rate": out_sr, "channels": out_ch,
        "encoder": encoder, "profile": profile, "container": "m4b",
        "content": ctype, "want_he": want_he, "he_available": he_available,
        "lowpass_hz": min(cutoff + 500, out_sr // 2), "highpass_hz": 70,
        "denoise": "light" if info.get("noise_floor_db", -60) > -50 else "off",
        "vbr": True if speech > 0.6 and encoder.startswith("libfdk") else False,
        "reason": reason + ("" if he_available else " [LC-comp: install exhale for true HE savings]"),
        "est_size_mb": round(info.get("duration_sec", 3600) * tgt / 8 / 1024, 1),
    }
