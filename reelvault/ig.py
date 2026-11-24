"""Instagram helpers: read the cookie jar, read a profile's post count, and
build the gallery-dl command line we shell out to."""
import json
import os
import re
import subprocess
import sys
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def jar_cookies(jar_path):
    """Netscape jar -> list of (name, value)."""
    out = []
    if not os.path.exists(jar_path):
        return out
    with open(jar_path) as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 7:
                out.append((parts[5], parts[6]))
    return out


def cookie_header(jar_path):
    return "; ".join("%s=%s" % kv for kv in jar_cookies(jar_path))


def profile_total(jar_path, username):
    """Post count straight off the profile page (og:description / JSON blob)."""
    req = urllib.request.Request(
        "https://www.instagram.com/%s/" % username,
        headers={"Cookie": cookie_header(jar_path), "User-Agent": UA,
                 "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return 0
    for pat in (
        r'<meta property="og:description" content="([\d,\.]+)\s+Posts',
        r'"edge_owner_to_timeline_media":\{"count":(\d+)',
        r'([\d,]+)\s+posts',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    return 0


def logged_in(jar_path):
    return any(n in ("sessionid",) and v for n, v in jar_cookies(jar_path))


def gallery_dl_cmd(jar_path, username, dest, archive, quality="720p",
                   limit_n=0, sleep="6-11"):
    cmd = [sys.executable, "-m", "gallery_dl",
           "--cookies", jar_path,
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
    cmd.append("https://www.instagram.com/%s/" % username)
    return cmd


def have_gallery_dl():
    try:
        p = subprocess.run([sys.executable, "-m", "gallery_dl", "--version"],
                           capture_output=True, text=True, timeout=20)
        return p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[-1] if (p.stdout or p.stderr) else ""
    except Exception as e:
        return False, str(e)


def have_ytdlp():
    try:
        import yt_dlp
        return True, getattr(yt_dlp.version, "__version__", "?")
    except Exception:
        return False, ""
