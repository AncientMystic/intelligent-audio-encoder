"""Probe: ffprobe metadata + spectral cutoff/noise/speech estimate."""
from __future__ import annotations
from dataclasses import dataclass, field
import json, shutil, subprocess

@dataclass
class ProbeResult:
    path: str
    codec: str = "unknown"
    bitrate_kbps: float = 0
    sample_rate: int = 0
    channels: int = 0
    duration_sec: float = 0
    cutoff_hz: int = 0  # measured -3dB point, 0 = unknown
    noise_floor_db: float = -60.0
    speech_score: float = 0.8  # 0 music .. 1 speech
    tags: dict = field(default_factory=dict)
    chapters: int = 0
    note: str = ""

def ffprobe_basic(path: str) -> ProbeResult:
    r = ProbeResult(path=path)
    exe = shutil.which("ffprobe") or shutil.which("ffmpeg")
    if not shutil.which("ffprobe"):
        r.note = "ffprobe not found; fill from MediaInfo/manual"
        return r
    try:
        p = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_streams", "-show_format", "-show_chapters", path],
                           capture_output=True, text=True, timeout=30)
        j = json.loads(p.stdout or "{}")
        streams = [s for s in j.get("streams", []) if s.get("codec_type") == "audio"]
        s = streams[0] if streams else {}
        r.codec = s.get("codec_name", "unknown")
        r.sample_rate = int(s.get("sample_rate", 0) or 0)
        r.channels = int(s.get("channels", 0) or 0)
        try: r.bitrate_kbps = float(s.get("bit_rate", 0) or 0) / 1000.0
        except Exception: pass
        fmt = j.get("format", {})
        try: r.duration_sec = float(fmt.get("duration", 0) or 0)
        except Exception: pass
        if not r.bitrate_kbps and r.duration_sec:
            try: r.bitrate_kbps = float(fmt.get("bit_rate", 0) or 0) / 1000.0
            except Exception: pass
        r.tags = fmt.get("tags", {}) or {}
        r.chapters = len(j.get("chapters", []) or [])
    except Exception as e:
        r.note = f"ffprobe failed: {e}"
    return r

def estimate_cutoff(path: str, seconds: int = 30, backend: str = "cpu", gpu: int = 0, seek_sec: int = 300) -> int:
    """Decode `seconds` from middle (avoid silent intro) and find brickwall cutoff.

    Old -3dB-from-peak failed on speech (HF naturally 15dB down) -> returned 263Hz.
    New: find highest bin staying above -25dB from passband average + 10dB above noise floor.
    """
    import shutil, subprocess, tempfile, os
    if not shutil.which("ffmpeg"):
        return 0
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tf:
            tmp = tf.name
        # seek past intro silence/credits for long books; clamp for short files
        ss = str(seek_sec) if seek_sec > 0 else "0"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", ss, "-t", str(seconds), "-i", path,
                        "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", tmp],
                       check=True, timeout=90)
        import numpy as np
        pcm = np.fromfile(tmp, dtype=np.float32)
        if pcm.size < 22050 * 3 and ss != "0":
            # short file: seek was past EOF — decode from the start instead
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-t", str(seconds), "-i", path,
                            "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", tmp],
                           check=True, timeout=90)
            pcm = np.fromfile(tmp, dtype=np.float32)
        try: os.unlink(tmp)
        except Exception: pass
        if pcm.size < 22050 * 3:
            return 0
        # trim leading digital silence (intro gaps skew spectrum)
        abspcm = np.abs(pcm)
        voiced = np.where(abspcm > 0.02)[0]
        if len(voiced) > 22050 * 2:
            pcm = pcm[voiced[0]:voiced[0] + min(len(pcm)-voiced[0], 22050*seconds)]
        # average magnitude spectrum over Hann-windowed 4096 frames
        n = (len(pcm) // 4096) * 4096
        if n < 4096 * 4:
            return 0
        w = pcm[:n].reshape(-1, 4096).astype(np.float64) * np.hanning(4096)
        mag = np.abs(np.fft.rfft(w, axis=1)).mean(axis=0)  # 2049 bins, 0..11025Hz
        mag = mag + 1e-12
        peak = mag[5:].max()
        # passband reference = median of 500Hz-6kHz (bins ~93..1116) — stable speech band
        ref = float(np.median(mag[93:1117]))
        if ref <= 0:
            ref = peak
        db = 20 * np.log10(mag / ref)
        # noise floor = median of top octave 9.5-11kHz
        noise_db = float(np.median(db[1760:]))
        # cutoff = highest bin with db > max(-25, noise+10), requiring 3-bin sustain (brickwall guard)
        thresh = max(-25.0, noise_db + 10.0)
        bins = len(mag)
        sr = 22050
        for i in range(bins - 4, 20, -1):
            if db[i] > thresh and db[i+1] > thresh - 3 and db[i+2] > thresh - 6:
                return int(i * (sr / 2) / (bins - 1))
        # fallback: 95% energy rolloff
        power = mag ** 2
        cumsum = np.cumsum(power) / power.sum()
        idx = int(np.searchsorted(cumsum, 0.95))
        return int(idx * (sr / 2) / (bins - 1))
    except Exception:
        try:
            if tmp and os.path.exists(tmp): os.unlink(tmp)
        except Exception: pass
        return 0


def analyze_content(path: str, seconds: int = 30, seek_sec: int = 600) -> dict:
    """Light content features to separate tts_monotone / natural_voice / full_cast.

    Returns dyn_range_db (std of 1s RMS), hf_ratio (energy >6kHz / total),
    silence_pct, crest. No heavy ML — fast numpy, GPU optional later.
    """
    import shutil, subprocess, tempfile, os
    out = {"dyn_range_db": 6.0, "hf_ratio": 0.05, "silence_pct": 10.0, "ok": False}
    if not shutil.which("ffmpeg"):
        return out
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tf:
            tmp = tf.name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(seek_sec), "-t", str(seconds),
                        "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", tmp],
                       check=True, timeout=90)
        import numpy as np
        pcm = np.fromfile(tmp, dtype=np.float32)
        if pcm.size < 22050 * 5 and seek_sec > 0:
            # short file: seek was past EOF — decode from the start instead
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0", "-t", str(seconds),
                            "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "22050", "-f", "f32le", tmp],
                           check=True, timeout=90)
            pcm = np.fromfile(tmp, dtype=np.float32)
        try: os.unlink(tmp)
        except Exception: pass
        if pcm.size < 22050 * 5:
            return out
        # 1s RMS dynamics
        n1 = (len(pcm) // 22050) * 22050
        rms = np.sqrt((pcm[:n1].reshape(-1, 22050).astype(np.float64) ** 2).mean(axis=1) + 1e-12)
        db = 20 * np.log10(rms + 1e-9)
        db_v = db[rms > 0.005]  # ignore pure silence for dynamics
        out["dyn_range_db"] = float(np.std(db_v)) if len(db_v) > 3 else 6.0
        out["silence_pct"] = float((rms < 0.005).mean() * 100)
        # HF ratio via single FFT average
        n = (len(pcm) // 4096) * 4096
        w = pcm[:n].reshape(-1, 4096).astype(np.float64) * np.hanning(4096)
        mag = np.abs(np.fft.rfft(w, axis=1)).mean(axis=0)
        power = mag ** 2
        # >6kHz bins start ~1116/2048
        out["hf_ratio"] = float(power[1116:].sum() / (power.sum() + 1e-12))
        out["crest"] = float(np.abs(pcm).max() / (np.sqrt((pcm.astype(np.float64)**2).mean()) + 1e-9))
        out["ok"] = True
        return out
    except Exception:
        try:
            if tmp and os.path.exists(tmp): os.unlink(tmp)
        except Exception: pass
        return out


def classify_content(info: dict, feats: dict | None = None) -> str:
    """tts_monotone | natural_voice | full_cast. Uses ffprobe + light feats + manual override."""
    if info.get("content_hint") in ("tts_monotone", "natural_voice", "full_cast"):
        return info["content_hint"]
    ch = int(info.get("channels", 1))
    sr = int(info.get("sample_rate", 22050))
    br = float(info.get("bitrate_kbps", 32))
    dyn = float((feats or {}).get("dyn_range_db", 6.0))
    hf = float((feats or {}).get("hf_ratio", 0.05))
    # Full-cast cues: stereo, high SR/bitrate, wide dynamics + HF (music/SFX)
    if ch >= 2 and (sr >= 32000 or br >= 96) and (dyn > 7.5 or hf > 0.12):
        return "full_cast"
    if br >= 128 or (ch >= 2 and sr >= 44100):
        return "full_cast"
    # TTS monotone cues: very flat dynamics, low HF variance, low bitrate mono
    if dyn < 3.5 and ch == 1 and br <= 48:
        return "tts_monotone"
    return "natural_voice"
