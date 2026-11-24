"""Source detection and command construction.

One library per source. Three kinds:

  instagram  a profile handle or instagram.com/<user> URL   -> gallery-dl
  youtube    a channel/@handle (we attach /shorts)          -> yt-dlp
  link       any single video/page URL yt-dlp understands   -> yt-dlp
"""
import os
import re
import sys

IG_HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
YT_SHORTS_VIDEO = re.compile(r"youtube\.com/shorts/[\w-]{6,}", re.I)


def _yt_label(url):
    m = re.search(r"/(@[\w.\-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/channel/([\w-]+)", url)
    if m:
        return "channel-" + m.group(1)
    m = re.search(r"/(?:c|user)/([\w.\-]+)", url)
    if m:
        return m.group(1)
    return "youtube"


def detect(raw):
    """Return (kind, target, label). Raises ValueError on nonsense input."""
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        raise ValueError("nothing to add")
    low = s.lower()

    if "youtu.be/" in low or "youtube.com" in low:
        if YT_SHORTS_VIDEO.search(s):
            return "link", s, "short-" + _video_id(s)
        if "/watch" in low or "/embed/" in low:
            return "link", s, "video-" + _video_id(s)
        base = s.split("?")[0].rstrip("/")
        base = re.sub(r"/(videos|streams|featured|playlists|about)$", "", base)
        if not base.lower().endswith("/shorts"):
            base += "/shorts"
        return "youtube", base, _yt_label(base)

    if "instagram.com" in low:
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", s)
        seg = (m.group(1) if m else "").lower()
        if seg in ("reel", "reels", "p", "tv", "stories"):
            return "link", s, "ig-post"
        if m:
            return "instagram", m.group(1), m.group(1)

    if low.startswith("http://") or low.startswith("https://"):
        return "link", s, "link-" + re.sub(r"[^a-z0-9]+", "-", low.split("//")[-1])[:28].strip("-")

    handle = s.lstrip("@/").rstrip("/")
    if IG_HANDLE.match(handle):
        return "instagram", handle, handle
    raise ValueError("unrecognised source — use @handle, a YouTube channel, or a URL")


def _video_id(url):
    m = re.search(r"shorts/([\w-]{6,})", url) or re.search(r"v=([\w-]{6,})", url) \
        or re.search(r"youtu\.be/([\w-]{6,})", url)
    return m.group(1)[:11] if m else "video"


def _quality_filter(quality):
    """Prefer H.264/AAC mp4 — AV1 and VP9 files won't open in QuickTime or older
    Safari, which makes an archive much less useful."""
    cap = {"720p": "[height<=720]", "1080p": "[height<=1080]"}.get(quality, "")
    return ("bv*{cap}[vcodec^=avc1]+ba[ext=m4a]/"
            "bv*{cap}[ext=mp4]+ba[ext=m4a]/"
            "b{cap}[ext=mp4]/"
            "bv*{cap}+ba/b{cap}/b").format(cap=cap)


def build_command(acc, jar, dest, archive, quality="720p", limit_n=0, sleep="6-11"):
    """acc: dict with keys kind, username, target."""
    kind = acc.get("kind") or "instagram"
    target = acc.get("target") or acc.get("username")
    ff = os.environ.get("REELVAULT_FFMPEG", "")

    if kind == "instagram":
        cmd = [sys.executable, "-m", "gallery_dl",
               "--cookies", jar,
               "--download-archive", archive,
               "--write-metadata",
               "-R", "5",
               "-o", "instagram.count=33",
               "--sleep-request", sleep,
               "-d", dest]
        if limit_n:
            cmd += ["--range", "1-%d" % limit_n]
        if quality == "1080p":
            cmd += ["-o", "downloader=ytdl"]
        cmd.append("https://www.instagram.com/%s/" % target)
        return cmd

    # youtube channel (shorts tab) or a single link
    from .ytdlp import ytdlp_cmd
    cmd = ytdlp_cmd() + [
           "--no-warnings", "--ignore-errors",
           "--download-archive", archive,
           "--write-info-json", "--write-thumbnail",
           "--no-overwrites",
           "-f", _quality_filter(quality),
           "--format-sort", "vcodec:h264,res,acodec:m4a",
           "--merge-output-format", "mp4",
           "--sleep-requests", "1.5", "--sleep-interval", "2",
           "--retries", "5", "--fragment-retries", "5",
           "-o", os.path.join(dest, "%(upload_date>%Y-%m-%d)s_%(id)s_%(title).70B.%(ext)s")]
    if ff:
        cmd += ["--ffmpeg-location", ff]
    if kind == "link":
        cmd += ["--no-playlist"]
    elif limit_n:
        cmd += ["--playlist-end", str(limit_n)]
    cmd.append(target)
    return cmd


def kind_badge(kind):
    return {"instagram": "IG", "youtube": "YT", "link": "LINK"}.get(kind, "?")
