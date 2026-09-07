"""Enrichment: identify book from tags/path, fetch candidates + cover. Offline-first, opt-in network."""
from __future__ import annotations
import os, re, requests

def guess_from_path(path: str) -> dict:
    parts = os.path.abspath(path).split(os.sep)
    file_base = os.path.splitext(os.path.basename(path))[0]
    # Heuristic: .../Author/Book/file.mp3 or .../Book/file.mp3
    book = parts[-2] if len(parts) >= 2 else file_base
    author = parts[-3] if len(parts) >= 3 else ""
    return {"author": author, "title": book, "file_title": file_base}

def search_openlibrary(title: str, author: str = "", limit: int = 5) -> list[dict]:
    try:
        r = requests.get("https://openlibrary.org/search.json",
                         params={"title": title, "author": author, "limit": limit}, timeout=15)
        docs = r.json().get("docs", [])[:limit]
        out = []
        for d in docs:
            cover_id = d.get("cover_i")
            out.append({"source": "openlibrary", "title": d.get("title"), "author": ", ".join(d.get("author_name", []) or []),
                        "year": d.get("first_publish_year"),
                        "cover": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None,
                        "key": d.get("key")})
        return out
    except Exception:
        return []

def search_googlebooks(title: str, author: str = "", limit: int = 5) -> list[dict]:
    try:
        q = f"intitle:{title}" + (f"+inauthor:{author}" if author else "")
        r = requests.get("https://www.googleapis.com/books/v1/volumes", params={"q": q, "maxResults": limit}, timeout=15)
        out = []
        for it in r.json().get("items", [])[:limit]:
            vi = it.get("volumeInfo", {})
            out.append({"source": "googlebooks", "title": vi.get("title"), "author": ", ".join(vi.get("authors", []) or []),
                        "year": (vi.get("publishedDate") or "")[:4],
                        "cover": (vi.get("imageLinks", {}) or {}).get("thumbnail"), "key": it.get("id")})
        return out
    except Exception:
        return []

def download_cover(url: str, dest_dir: str) -> str | None:
    try:
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, "cover.jpg")
        if os.path.exists(dest):
            return dest  # never overwrite silently
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 5000:
            with open(dest, "wb") as f:
                f.write(r.content)
            return dest
    except Exception:
        pass
    return None
