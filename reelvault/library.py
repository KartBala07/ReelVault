"""Library maintenance: flatten gallery-dl's nested output and give each file a
readable, sortable name built from real metadata."""
import glob
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime


def pretty_likes(n):
    if not isinstance(n, int) or n <= 0:
        return "0-likes"
    if n >= 1_000_000:
        return "%.1fM-likes" % (n / 1_000_000.0)
    if n >= 1_000:
        return "%.1fk-likes" % (n / 1000.0)
    return "%d-likes" % n


def slug(text, limit=48):
    text = (text or "").strip().split("\n")[0]
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#@]\w+", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text[:limit].strip("-")


def parse_date(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime((s or "")[:19], fmt)
        except Exception:
            pass
    return None


def flatten(lib_dir):
    """Move media/metadata out of gallery-dl's nested dirs into the library root."""
    moved = 0
    for root, dirs, files in os.walk(lib_dir):
        rel = os.path.relpath(root, lib_dir)
        if rel == ".":
            continue
        if any(part.startswith(".") for part in rel.split(os.sep)):
            continue
        for f in files:
            if not f.lower().endswith((".mp4", ".jpg", ".jpeg", ".webp", ".png", ".json")):
                continue
            src = os.path.join(root, f)
            dst = os.path.join(lib_dir, f)
            if os.path.exists(dst):
                continue
            try:
                shutil.move(src, dst)
                moved += 1
            except Exception:
                pass
    # prune empty nested dirs
    base_abs = os.path.abspath(lib_dir)
    for root, dirs, files in os.walk(lib_dir, topdown=False):
        if os.path.abspath(root) == base_abs:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass
    return moved


def title_all(lib_dir):
    """Rename <id>.mp4 (+ .mp4.json) to date_likes_shortcode[_caption].mp4."""
    renamed = 0
    seen = set()
    for path in sorted(glob.glob(os.path.join(lib_dir, "*.mp4"))):
        meta = {}
        jpath = path + ".json"
        # yt-dlp already names files "<date>_<id>_<title>.mp4" — leave those alone
        if os.path.exists(path[:-4] + ".info.json") or os.path.exists(path + ".info.json"):
            continue
        if os.path.exists(jpath):
            try:
                meta = json.load(open(jpath))
            except Exception:
                meta = {}
        d = parse_date(meta.get("date")) or datetime.fromtimestamp(os.path.getmtime(path))
        sc = meta.get("shortcode") or os.path.basename(path)[:-4]
        parts = [d.strftime("%Y-%m-%d"), pretty_likes(meta.get("likes")), sc]
        cap = slug(meta.get("description") or "")
        if cap and cap.lower() != "original-audio":
            parts.append(cap)
        base = "_".join(p for p in parts if p)
        uniq, i = base, 2
        while uniq in seen:
            uniq = "%s-%d" % (base, i)
            i += 1
        seen.add(uniq)
        newbase = os.path.join(lib_dir, uniq)
        if newbase + ".mp4" == path:
            continue
        if os.path.exists(newbase + ".mp4"):
            continue
        try:
            os.rename(path, newbase + ".mp4")
            if os.path.exists(jpath):
                os.rename(jpath, newbase + ".json")
            renamed += 1
        except Exception:
            pass
    return renamed


def _meta_for(mp4_path):
    """Sidecar metadata for either downloader.

    gallery-dl writes <name>.mp4.json (or <name>.json after retitling);
    yt-dlp writes <name>.info.json. Both are normalised to the instagram shape.
    """
    stem = mp4_path[:-4]
    for cand in (mp4_path + ".json", stem + ".json",
                 stem + ".info.json", mp4_path + ".info.json"):
        if os.path.exists(cand):
            try:
                return _normalise(json.load(open(cand)))
            except Exception:
                return {}
    return {}


def _normalise(meta):
    """yt-dlp info.json -> the field names the rest of the app expects."""
    if not isinstance(meta, dict):
        return {}
    if meta.get("upload_date") or meta.get("webpage_url"):
        raw = str(meta.get("upload_date") or "")
        date = ("%s-%s-%s 00:00:00" % (raw[:4], raw[4:6], raw[6:8])) if len(raw) == 8 else ""
        return {
            "description": meta.get("description") or "",
            "title": meta.get("title"),
            "date": date,
            "likes": meta.get("like_count") or 0,
            "shortcode": meta.get("id") or "",
            "post_url": meta.get("webpage_url") or "",
            "tags": meta.get("tags") or [],
            "audio_title": None,
        }
    return meta


HASHTAG_RE = re.compile(r"#([\w\u00c0-\u024f]{1,60})", re.UNICODE)


def caption_of(meta):
    return (meta.get("description") or meta.get("title") or "").strip()


def hashtags_of(meta):
    seen, out = set(), []
    for tag in HASHTAG_RE.findall(caption_of(meta)):
        low = tag.lower()
        if low not in seen:
            seen.add(low)
            out.append(tag)
    # yt-dlp exposes YouTube tags separately from the description
    for tag in (meta.get("tags") or []):
        tag = str(tag).strip().replace(" ", "")
        low = tag.lower()
        if tag and low not in seen:
            seen.add(low)
            out.append(tag)
    return out


def prune_playlist_meta(lib_dir):
    """yt-dlp writes playlist-level metadata (NA_<channel>.info.json/.jpg) when the
    source is a channel tab — drop it so the library is only real media."""
    removed = 0
    try:
        names = os.listdir(lib_dir)
    except OSError:
        return 0
    for name in names:
        if name.startswith("NA_") and (name.endswith(".info.json") or name.endswith(".jpg")
                                       or name.endswith(".webp")):
            try:
                os.remove(os.path.join(lib_dir, name))
                removed += 1
            except OSError:
                pass
    return removed


def shortcode_of(mp4_path):
    """Best-effort shortcode for a library file (metadata first, filename as fallback)."""
    meta = _meta_for(mp4_path)
    if meta.get("shortcode"):
        return meta["shortcode"]
    m = re.search(r"_([A-Za-z0-9_-]{8,})\.mp4$", os.path.basename(mp4_path))
    return m.group(1) if m else os.path.basename(mp4_path)[:-4]


def library(lib_dir):
    """Scan the library: metadata-rich records for the grid, table and CSV."""
    items = []
    for path in sorted(glob.glob(os.path.join(lib_dir, "*.mp4"))):
        name = os.path.basename(path)
        meta = _meta_for(path)
        cap = caption_of(meta)
        first_line = cap.split("\n")[0].strip()
        real_title = (meta.get("title") or "").strip()
        audio = (meta.get("audio_title") or "").strip()
        if audio.lower() in ("original audio", "original-audio", "original sound"):
            audio = ""
        st = os.stat(path)
        items.append({
            "file": name,
            "bytes": st.st_size,
            "mtime": st.st_mtime,
            "date": (meta.get("date") or "")[:19],
            "likes": meta.get("likes") or 0,
            "shortcode": meta.get("shortcode") or "",
            # a real title (YouTube) beats a caption line, which beats the filename
            "title": (real_title or first_line or name[:-4])[:120],
            "caption": cap or real_title,
            "hashtags": hashtags_of(meta),
            "audio": audio,
            "has_caption": bool(cap),
            "url": meta.get("post_url") or "",
        })
    items.sort(key=lambda x: x["date"] or "", reverse=True)
    return items
