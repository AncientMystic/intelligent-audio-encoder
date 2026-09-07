"""Batch queue: SQLite jobs, file-level parallel only (1 book file = 1 job). In-place (.bak) vs mirror-tree."""
from __future__ import annotations
import os, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

@dataclass
class Job:
    src: str
    dst: str
    profile: str = "transparent"
    status: str = "queued"

SCHEMA = "CREATE TABLE IF NOT EXISTS jobs(src TEXT PRIMARY KEY, dst TEXT, profile TEXT, status TEXT, msg TEXT)"

class QueueDB:
    def __init__(self, path: str = "queue.db"):
        self.path = path
        self._l = threading.Lock()
        c = sqlite3.connect(path); c.execute(SCHEMA); c.commit(); c.close()
    def add(self, src: str, dst: str, profile="transparent"):
        with self._l:
            c = sqlite3.connect(self.path)
            c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)", (src, dst, profile, "queued", ""))
            c.commit(); c.close()
    def set(self, src: str, status: str, msg: str = ""):
        with self._l:
            c = sqlite3.connect(self.path)
            c.execute("UPDATE jobs SET status=?, msg=? WHERE src=?", (status, msg, src))
            c.commit(); c.close()

def mirror_dst(src: str, src_root: str, dst_root: str, new_ext: str = ".m4b") -> str:
    rel = os.path.relpath(os.path.abspath(src), os.path.abspath(src_root))
    base, _ = os.path.splitext(rel)
    return os.path.join(os.path.abspath(dst_root), base + new_ext)


def resolve_dest(src: str, src_root: str, mode: str = "source",
                 output_dir: str | None = None, new_ext: str = ".m4b") -> str:
    """Destination for one job.

    mode="source": sibling of src with new extension (in-place family; source
      files are only ever replaced when extensions already match, via .bak).
    mode="output": mirrored tree under output_dir, same relative structure.
    """
    if mode == "output" and output_dir:
        return mirror_dst(src, src_root, output_dir, new_ext)
    return os.path.splitext(os.path.abspath(src))[0] + new_ext


def collect_jobs(src_root: str, dst_root: str | None, exts=(".mp3", ".m4a", ".m4b", ".flac", ".wav", ".ogg", ".wma")) -> list[Job]:
    jobs: list[Job] = []
    for dp, _, fns in os.walk(src_root):
        for fn in fns:
            if fn.lower().endswith(exts):
                src = os.path.join(dp, fn)
                if dst_root:
                    dst = mirror_dst(src, src_root, dst_root)
                else:
                    dst = os.path.splitext(src)[0] + ".m4b"  # in-place sibling; atomic replace with .bak at encode time
                jobs.append(Job(src, dst))
    return jobs


def collect_jobs_with_mode(src_root: str, mode: str = "source",
                           output_dir: str | None = None,
                           exts=(".mp3", ".m4a", ".m4b", ".flac", ".wav", ".ogg", ".wma")) -> list[Job]:
    """Same as collect_jobs but driven by the GUI destination control."""
    jobs: list[Job] = []
    for dp, _, fns in sorted(os.walk(src_root)):
        for fn in sorted(fns):
            if fn.lower().endswith(exts):
                src = os.path.join(dp, fn)
                jobs.append(Job(src, resolve_dest(src, src_root, mode, output_dir)))
    return jobs


def _apply_codec_override(rec: dict, codec: str, avail: dict) -> dict:
    """Force a GUI codec choice onto an advisor recipe. 'Auto' leaves it alone."""
    rec = dict(rec)
    if codec.startswith("Auto"):
        return rec
    if codec == "MP3":
        rec.update(encoder="libmp3lame", profile="mp3", want_he=False)
    elif codec == "AAC-LC (max compatibility)":
        rec.update(encoder="fdkaac" if avail.get("fdkaac") else "aac",
                   profile="aac_low", want_he=False)
    elif codec == "xHE-AAC (smallest files)":
        # mono and stereo both via exhale; needs a modern player (Android 9+, recent iOS)
        rec.update(encoder="exhale", profile="he_aac", want_he=True)
    elif codec == "HE-AAC (compatible)":
        # universally playable HE-AAC via fdkaac; note it emits stereo from mono sources
        rec.update(encoder="fdkaac" if avail.get("fdkaac") else "exhale",
                   profile="he_aac", want_he=True)
    elif codec == "HE-AACv2 (stereo low)":
        rec.update(encoder="fdkaac" if avail.get("fdkaac") else "exhale",
                   profile="he_aac_v2", want_he=True)
    return rec


def _as_lc_retry(rec: dict, avail: dict) -> dict:
    """QA-failure fallback: SBR off, plain LC, +8k headroom."""
    rec = dict(rec)
    rec.update(encoder="fdkaac" if avail.get("fdkaac") else "aac",
               profile="aac_low", want_he=False,
               target_kbps=min(320, rec["target_kbps"] + 8))
    rec["reason"] = (rec.get("reason", "") + " [QA retry: SBR off LC +8k]").strip()
    return rec


def run_job(src: str, dst: str, rate_setting: dict, codec: str = "Auto (advisor picks)",
            backend: str = "cpu", gpu_index: int = 0, qa_pct: int = 5,
            cancel_event=None) -> dict:
    """Run the full pipeline for one file. Returns a result dict for the GUI table.

    Stages: probe -> cutoff/classify -> advise (+codec override) -> encode
    (pipe, or direct ffmpeg for MP3) -> tags -> QA -> one LC retry on FAIL.
    Same-path outputs (e.g. .m4b in source mode) encode to temp then swap via .bak.
    """
    import shutil
    import subprocess
    import tempfile
    from core.advisor import advise_for_sample
    from core.encoder import build_ffmpeg_cmd, check_ffmpeg
    from core.encoder_pipe import encode_file
    from core.probe import analyze_content, classify_content, estimate_cutoff, ffprobe_basic
    from core.tags import copy_tags_and_art
    from core.validator import quick_check

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    result: dict = {"src": src, "dst": dst, "status": "failed", "note": ""}
    try:
        if cancelled():
            result.update(status="cancelled", note="cancelled before probe")
            return result
        probe = ffprobe_basic(src)
        if not probe.sample_rate or not probe.channels:
            result["note"] = "ffprobe found no audio stream"
            return result
        cutoff = estimate_cutoff(src, seconds=30, backend=backend if backend != "cpu" else "cpu")
        feats = analyze_content(src)
        content = classify_content({
            "bitrate_kbps": probe.bitrate_kbps, "sample_rate": probe.sample_rate,
            "channels": probe.channels, "cutoff_hz": cutoff}, feats)
        avail = check_ffmpeg()
        avail["_channels"] = probe.channels
        info = {"bitrate_kbps": probe.bitrate_kbps or 64, "sample_rate": probe.sample_rate,
                "channels": probe.channels, "duration_sec": probe.duration_sec or 3600,
                "cutoff_hz": cutoff, "speech_score": 0.9, "content": content}
        rec = advise_for_sample(info, mode="transparent", avail=avail, content=content)
        m = (rate_setting or {}).get("mode", "transparent")
        if m == "manual kbps":
            rec = advise_for_sample(info, manual_kbps=int(rate_setting.get("bitrate_kbps", 64)),
                                    avail=avail, content=content)
        elif m == "% of source":
            rec = advise_for_sample(info, pct=int(rate_setting.get("percent", 75)),
                                    avail=avail, content=content)
        elif m in ("transparent", "maximum_saving", "archive"):
            rec = advise_for_sample(info, mode=m, avail=avail, content=content)
        rec = _apply_codec_override(rec, codec, avail)
        result.update(target_kbps=rec["target_kbps"], encoder=rec["encoder"],
                      content=content, cutoff_hz=cutoff)
        if rec.get("encoder") == "libmp3lame":
            dst = os.path.splitext(dst)[0] + ".mp3"
            result["dst"] = dst
        if cancelled():
            result.update(status="cancelled", note="cancelled before encode")
            return result

        same_path = os.path.abspath(dst) == os.path.abspath(src)
        work_dst = dst + ".tmp-new" if same_path else dst
        if not same_path and os.path.dirname(os.path.abspath(work_dst)):
            os.makedirs(os.path.dirname(os.path.abspath(work_dst)), exist_ok=True)
        src_mb = os.path.getsize(src) / 1024 ** 2
        if rec.get("encoder") == "libmp3lame":
            cmd = build_ffmpeg_cmd(src, work_dst, rec)
            subprocess.run(cmd, check=True, timeout=3600)
            tool = "ffmpeg-lame"
        else:
            tool = encode_file(src, work_dst, rec).get("tool", "?")
        result["tool"] = tool
        try:
            rp = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                 "-show_entries", "stream=channels",
                                 "-of", "default=noprint_wrappers=1:nokey=1", work_dst],
                                capture_output=True, text=True, timeout=30)
            out_ch = int((rp.stdout or "0").strip() or 0)
            if out_ch and out_ch != rec["channels"]:
                result["note"] += (f"channels {rec['channels']}->{out_ch} "
                                   f"({rec['encoder']} container quirk); ")
        except Exception:
            pass
        if same_path:
            bak = src + ".bak"
            if os.path.exists(bak):
                os.unlink(bak)
            os.rename(src, bak)
            os.replace(work_dst, dst)
            result["note"] = f"replaced in place (backup {os.path.basename(bak)}); "
        try:
            tag_rep = copy_tags_and_art(src if not same_path else bak, dst)
            result["tags"] = tag_rep
        except Exception as e:
            result["note"] += f"tags warning: {e}; "

        qa = quick_check(src if not same_path else bak, dst, pct=qa_pct)
        attempt = 1
        if qa.get("status") == "FAIL" and rec.get("encoder") != "libmp3lame":
            retry_rec = _as_lc_retry(rec, avail)
            try:
                if same_path:
                    work2 = dst + ".tmp-retry"
                    encode_file(bak, work2, retry_rec)
                    os.replace(work2, dst)
                    qa = quick_check(bak, dst, pct=qa_pct)
                else:
                    encode_file(src, dst, retry_rec)
                    qa = quick_check(src, dst, pct=qa_pct)
                attempt = 2
                rec = retry_rec
                result.update(target_kbps=rec["target_kbps"], encoder=rec["encoder"])
                result["note"] += "QA retry (LC +8k) applied; "
            except Exception as e:
                result["note"] += f"QA retry failed: {e}; "
        result["qa"] = qa
        result["attempts"] = attempt
        try:
            dst_mb = os.path.getsize(dst) / 1024 ** 2
        except OSError:
            dst_mb = 0.0
        result.update(src_mb=round(src_mb, 1), dst_mb=round(dst_mb, 1),
                      saved_pct=round((1 - dst_mb / src_mb) * 100, 1) if src_mb else 0.0)
        result["status"] = "done"
        result["note"] += (f"{rec['target_kbps']}k {rec['encoder']} ({content}), "
                           f"{result['src_mb']}→{result['dst_mb']}MB ({result['saved_pct']}%), "
                           f"QA {qa.get('status')}: {qa.get('reason', '')}")
        return result
    except subprocess.CalledProcessError as e:
        result["note"] = f"encoder failed (exit {e.returncode})"
        return result
    except Exception as e:
        result["note"] = f"error: {e}"
        return result
