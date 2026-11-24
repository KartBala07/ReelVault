"""Locate a usable yt-dlp: the standalone binary is much newer than the module
pinned to an old Python, and YouTube breaks old builds constantly."""
import os
import shutil
import subprocess
import sys

_CACHE = {}


def ytdlp_cmd():
    """Return the argv prefix for yt-dlp, preferring a recent standalone binary."""
    if "cmd" in _CACHE:
        return _CACHE["cmd"]

    candidates = []
    env = os.environ.get("REELVAULT_YTDLP")
    if env:
        candidates.append([env])
    for path in (
        os.path.expanduser("~/.local/bin/yt-dlp"),
        "/opt/homebrew/bin/yt-dlp",
        "/usr/local/bin/yt-dlp",
    ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            candidates.append([path])
    found = shutil.which("yt-dlp")
    if found:
        candidates.append([found])

    for cand in candidates:
        try:
            p = subprocess.run(cand + ["--version"], capture_output=True, text=True, timeout=20)
            if p.returncode == 0 and p.stdout.strip():
                _CACHE["cmd"] = cand
                _CACHE["version"] = p.stdout.strip()
                return cand
        except Exception:
            continue

    _CACHE["cmd"] = [sys.executable, "-m", "yt_dlp"]
    return _CACHE["cmd"]


def ytdlp_version():
    ytdlp_cmd()
    return _CACHE.get("version", "?")
