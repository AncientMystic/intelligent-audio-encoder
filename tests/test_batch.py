import math
import os
import struct
import wave

from core.queue import collect_jobs_with_mode, resolve_dest
from core.validator import quick_check


def _sine_wav(path: str, seconds: float = 6.0, freq: float = 440.0, sr: int = 22050, amp: float = 0.4):
    n = int(seconds * sr)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * t / sr)))
                          for t in range(n))
        wf.writeframes(frames)


def _silence_wav(path: str, seconds: float = 6.0, sr: int = 22050):
    n = int(seconds * sr)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n)


def test_resolve_dest_source_mode(tmp_path=None):
    import tempfile
    root = tempfile.mkdtemp()
    src = os.path.join(root, "Book", "ch01.mp3")
    os.makedirs(os.path.dirname(src))
    open(src, "wb").close()
    assert resolve_dest(src, root, "source") == os.path.splitext(os.path.abspath(src))[0] + ".m4b"


def test_resolve_dest_output_mode_keeps_tree():
    import tempfile
    root = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    src = os.path.join(root, "Author", "Book", "ch 01.mp3")
    os.makedirs(os.path.dirname(src))
    open(src, "wb").close()
    dst = resolve_dest(src, root, "output", out)
    assert dst == os.path.join(os.path.abspath(out), "Author", "Book", "ch 01.m4b")


def test_collect_jobs_with_mode_counts_only_audio():
    import tempfile
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "B"))
    for name in ("a.mp3", "b.flac", "cover.jpg", "notes.txt"):
        open(os.path.join(root, "B", name), "wb").close()
    jobs = collect_jobs_with_mode(root, "source")
    assert sorted(os.path.basename(j.src) for j in jobs) == ["a.mp3", "b.flac"]


def test_quick_check_identical_passes(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.wav")
    b = os.path.join(d, "b.wav")
    _sine_wav(a)
    _sine_wav(b)
    res = quick_check(a, b, pct=100)
    assert res["status"] == "PASS", res


def test_quick_check_silence_output_fails():
    import tempfile
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.wav")
    b = os.path.join(d, "b.wav")
    _sine_wav(a)
    _silence_wav(b)
    res = quick_check(a, b, pct=100)
    assert res["status"] == "FAIL", res


def test_quick_check_missing_output_fails():
    import tempfile
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.wav")
    _sine_wav(a)
    res = quick_check(a, os.path.join(d, "nope.m4b"), pct=5)
    assert res["status"] == "FAIL", res


def test_quick_check_xhe_structural_only():
    import shutil
    import subprocess
    import tempfile
    from core.encoder_pipe import tool_paths
    exe = tool_paths()["exhale"]
    if not exe or not shutil.which("ffmpeg"):
        return  # needs the standalone toolchain
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.wav")
    _sine_wav(a, seconds=6.0)
    out = os.path.join(d, "a.m4a")
    subprocess.run([exe, "a", a, out], check=True, timeout=120)
    res = quick_check(a, out, pct=100)
    assert res["status"] == "PASS", res
    assert "structural" in res.get("reason", ""), res


def test_codec_override_labels():
    from core.queue import _apply_codec_override
    avail = {"exhale": True, "fdkaac": True}
    base = {"channels": 1, "target_kbps": 24}
    assert _apply_codec_override(dict(base), "Auto (advisor picks)", avail)["target_kbps"] == 24
    r = _apply_codec_override(dict(base), "xHE-AAC (smallest files)", avail)
    assert (r["encoder"], r["profile"]) == ("exhale", "he_aac"), r
    r = _apply_codec_override(dict(base), "HE-AAC (compatible)", avail)
    assert (r["encoder"], r["profile"]) == ("fdkaac", "he_aac"), r
    r = _apply_codec_override(dict(base), "AAC-LC (max compatibility)", avail)
    assert (r["encoder"], r["profile"]) == ("fdkaac", "aac_low"), r
    r = _apply_codec_override(dict(base), "MP3", avail)
    assert r["encoder"] == "libmp3lame", r
