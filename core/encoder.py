"""FFmpeg command builder with anti-hiss defaults. 1:1, keep native SR/ch unless advisor says downmix."""
from __future__ import annotations
import shutil

def _aac_encoder_wanted(wanted: str) -> str:
    """Map wanted encoder to available one. Prefers fdk, falls back to exhale/native."""
    return wanted  # resolved at runtime in available_encoders(); builder keeps intent

def build_ffmpeg_cmd(in_path: str, out_path: str, rec: dict, ffmpeg: str = "ffmpeg") -> list[str]:
    sr, ch = rec["sample_rate"], rec["channels"]
    tgt = rec["target_kbps"]
    enc = rec.get("encoder", "libfdk_aac")
    prof = rec.get("profile", "aac_low")

    if enc in ("exhale", "fdkaac", "libfdk_aac"):
        raise ValueError(
            f"encoder={enc!r} must go via core.encoder_pipe.encode_file "
            f"(ffmpeg -c:a {enc} does not exist; use tools/bin standalones). "
            f"Use encoder='aac' here for native fallback only.")

    af: list[str] = [f"highpass=f={rec.get('highpass_hz',70)}", f"lowpass=f={rec.get('lowpass_hz',10000)}"]
    if rec.get("denoise") == "light":
        af.append("afftdn=nr=6:nf=-25:nt=v")
    af_s = ",".join(af)

    cmd = [ffmpeg, "-v", "error", "-y", "-i", in_path, "-map", "0:a:0",
           "-map_metadata", "0", "-map_chapters", "0",
           "-ar", str(sr), "-ac", str(ch), "-af", af_s]
    if enc == "libfdk_aac":
        cmd += ["-c:a", "libfdk_aac", "-profile:a", prof]
        cmd += ["-vbr", "3" if rec.get("vbr") else "0", "-b:a", f"{tgt}k"] if rec.get("vbr") else ["-b:a", f"{tgt}k"]
        cmd += ["-afterburner", "1"]
    elif enc == "exhale":
        # exhale via ffmpeg -c:a exhale (if built) else external exhale binary path handled by caller
        cmd += ["-c:a", "exhale", "-b:a", f"{tgt}k"]
    elif enc == "libmp3lame":
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{tgt}k", "-joint_stereo", "1"]
    else:
        cmd += ["-c:a", "aac", "-b:a", f"{tgt}k", "-cutoff", str(rec.get("lowpass_hz", 14000))]
    cmd += ["-movflags", "+faststart", out_path]
    return cmd

def check_ffmpeg() -> dict:
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exhale_exe = os.path.join(root, "tools", "bin", "exhale", "exhale.exe")
    fdkaac_exe = os.path.join(root, "tools", "bin", "fdkaac", "fdkaac.exe")
    has_exhale = os.path.exists(exhale_exe)
    has_fdkaac = os.path.exists(fdkaac_exe)
    if not shutil.which("ffmpeg"):
        return {"ok": False, "msg": "ffmpeg not on PATH — run tools/fetch_ffmpeg.ps1",
                "exhale": has_exhale, "fdkaac": has_fdkaac}
    import subprocess
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15)
        t = r.stdout
        return {"ok": True, "libfdk_aac": "libfdk_aac" in t, "aac": " aac " in t,
                "libmp3lame": "libmp3lame" in t, "exhale": ("exhale" in t) or has_exhale,
                "fdkaac": has_fdkaac, "exhale_exe": exhale_exe if has_exhale else "",
                "fdkaac_exe": fdkaac_exe if has_fdkaac else ""}
    except Exception as e:
        return {"ok": False, "msg": str(e), "exhale": has_exhale, "fdkaac": has_fdkaac}
