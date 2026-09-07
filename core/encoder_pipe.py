"""Pipe encoder: ffmpeg decode+filters -> temp WAV -> exhale / fdkaac standalone -> m4b.

Why: gyan FFmpeg has no libfdk_aac (nonfree, undistributable). Standalones give us
true HE quality legally:
 - mono low (TTS 18k / natural 24k) -> exhale (preserves mono, xHE-AAC)
 - stereo/high + LC -> fdkaac -S --moov-before-mdat (preserves stereo, HE/LC)
 - fallback -> native aac via ffmpeg
fdkaac HE upmixes mono->stereo, so never route mono HE to fdkaac.
"""
from __future__ import annotations
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXHALE = os.path.join(ROOT, "tools", "bin", "exhale", "exhale.exe")
FDKAAC = os.path.join(ROOT, "tools", "bin", "fdkaac", "fdkaac.exe")

# exhale presets: 0-9 = 16*#+48 kbit/s stereo, a-g = 12*#+36 eSBR. Mono ~= half.
# verified on 22050Hz mono speech reference: preset 0 -> ~25.7k actual, preset a -> ~15.2k actual.
EXHALE_PRESET_FOR_KBPS_MONO = [(19, "a"), (22, "b"), (25, "0"), (32, "1"), (40, "2")]

def exhale_preset_for_mono(tgt_kbps: int) -> str:
    for ceiling, preset in EXHALE_PRESET_FOR_KBPS_MONO:
        if tgt_kbps <= ceiling:
            return preset
    return "2"


def tool_paths() -> dict:
    return {"exhale": EXHALE if os.path.exists(EXHALE) else "",
            "fdkaac": FDKAAC if os.path.exists(FDKAAC) else ""}


def decode_to_wav(src: str, sr: int, ch: int, af: str, ss: str = "", t: str = "") -> str:
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if ss:
        cmd += ["-ss", ss]
    if t:
        cmd += ["-t", t]
    cmd += ["-i", src, "-map", "0:a:0", "-ar", str(sr), "-ac", str(ch)]
    if af:
        cmd += ["-af", af]
    cmd += ["-c:a", "pcm_s16le", tmp]
    subprocess.run(cmd, check=True, timeout=300)
    return tmp


def encode_wav_with_exhale(wav: str, dst: str, preset: str) -> None:
    subprocess.run([EXHALE, preset, wav, dst], check=True, timeout=600)


def encode_wav_with_fdkaac(wav: str, dst: str, profile: int, bitrate: int) -> None:
    # -S silent (progress on stderr breaks PS), --moov-before-mdat required (else moov missing)
    subprocess.run([FDKAAC, "-S", "-p", str(profile), "-b", str(bitrate),
                    "--moov-before-mdat", "-o", dst, wav], check=True, timeout=600)


def encode_file(src: str, dst: str, rec: dict, ss: str = "", t: str = "") -> dict:
    """Full 1:1 encode per advisor recipe. Returns {"tool","preset/profile",...}."""
    sr, ch, tgt = rec["sample_rate"], rec["channels"], rec["target_kbps"]
    enc = rec.get("encoder", "aac")
    af = f"highpass=f={rec.get('highpass_hz',70)},lowpass=f={rec.get('lowpass_hz',10000)}"
    if rec.get("denoise") == "light":
        af += ",afftdn=nr=6:nf=-25:nt=v"
    wav = decode_to_wav(src, sr, ch, af, ss, t)
    if os.path.exists(dst):
        os.unlink(dst)  # exhale/fdkaac refuse to overwrite; ffmpeg -y handles its own path
    try:
        paths = tool_paths()
        if enc == "exhale" and paths["exhale"]:
            preset = exhale_preset_for_mono(tgt) if ch == 1 else "2"
            encode_wav_with_exhale(wav, dst, preset)
            return {"tool": "exhale", "preset": preset}
        if enc in ("fdkaac", "libfdk_aac") and paths["fdkaac"] and not (ch == 1 and rec.get("profile") == "he_aac"):
            # mono HE -> exhale (fdkaac upmixes mono->stereo); stereo HE + all LC -> fdkaac
            prof = 5 if rec.get("profile") == "he_aac" else (29 if rec.get("profile") == "he_aac_v2" else 2)
            encode_wav_with_fdkaac(wav, dst, prof, tgt * 1000)
            return {"tool": "fdkaac", "profile": prof}
        if enc == "exhale" and not paths["exhale"] and paths["fdkaac"] and ch == 2:
            prof = 5 if tgt <= 80 else 2
            encode_wav_with_fdkaac(wav, dst, prof, tgt * 1000)
            return {"tool": "fdkaac-fallback", "profile": prof}
        # native fallback
        from core.encoder import build_ffmpeg_cmd
        import shutil
        # re-encode directly from src (not wav) to keep tags path simple
        cmd = build_ffmpeg_cmd(src, dst, {**rec, "encoder": "aac", "profile": "aac_low"})
        subprocess.run(cmd, check=True, timeout=900)
        return {"tool": "native-aac"}
    finally:
        try:
            os.unlink(wav)
        except Exception:
            pass
