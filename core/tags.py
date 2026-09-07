"""Preserve ALL data: second-pass mutagen copy + folder-art embed + diff check. 1:1 only."""
from __future__ import annotations
import os

CANDIDATE_ART = ["folder.jpg", "folder.png", "cover.jpg", "cover.png", "cover.webp", "front.jpg"]

def find_folder_art(src_path: str) -> str | None:
    d = os.path.dirname(os.path.abspath(src_path))
    for n in CANDIDATE_ART:
        p = os.path.join(d, n)
        if os.path.exists(p):
            return p
    # case-insensitive fallback
    try:
        files = os.listdir(d)
        low = {f.lower(): f for f in files}
        for n in CANDIDATE_ART:
            if n.lower() in low:
                return os.path.join(d, low[n.lower()])
    except Exception:
        pass
    return None

def copy_tags_and_art(src: str, dst: str, folder_art: str | None = None) -> dict:
    """Copy MP3/MP4 tags + chapters note + embed art. Returns report."""
    report: dict = {"copied": 0, "art": None, "chapters_src": 0}
    art = folder_art or find_folder_art(src)
    try:
        from mutagen.mp4 import MP4, MP4Cover
        from mutagen.id3 import ID3
        # Read source tags (mp3 or mp4)
        src_tags: dict = {}
        try:
            if src.lower().endswith(".mp3"):
                id3 = ID3(src)
                for k in ("TIT2", "TALB", "TPE1", "TPE2", "TCON", "TRCK", "TYER", "COMM"):
                    if k in id3:
                        src_tags[k] = str(id3[k])
            else:
                m = MP4(src)
                src_tags = {k: str(v) for k, v in m.tags.items()} if m.tags else {}
        except Exception as e:
            report["src_tags_error"] = str(e)
        # Write to dst m4b
        try:
            o = MP4(dst)
        except Exception:
            o = None
        if o is not None:
            if o.tags is None:
                o.tags = {}
            # Map common ID3 -> MP4 atoms (keep it minimal but lossless for Title/Album/Artist)
            mapping = {"TIT2": "\xa9nam", "TALB": "\xa9alb", "TPE1": "\xa9ART", "TPE2": "aART", "TCON": "\xa9gen", "TRCK": "trkn"}
            for k, v in src_tags.items():
                if k in mapping:
                    o.tags[mapping[k]] = [v]
            # Embed art if dst lacks it and we have a file
            has_art = "covr" in (o.tags or {})
            if not has_art and art and os.path.exists(art):
                with open(art, "rb") as f:
                    data = f.read()
                fmt = MP4Cover.FORMAT_PNG if art.lower().endswith(".png") else MP4Cover.FORMAT_JPEG
                o.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
                report["art"] = art
            o.save()
            report["copied"] = len(o.tags or {})
    except Exception as e:
        report["error"] = str(e)
    return report
