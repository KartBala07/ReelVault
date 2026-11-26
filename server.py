#!/usr/bin/env python3
"""ReelVault — a local, open-source Instagram reel archiver with a web UI.

    python3 server.py            # http://127.0.0.1:8791
    python3 server.py --port 9000 --open

Binds to loopback only. Downloads run through gallery-dl using a cookie jar
imported from your own logged-in browser session.
"""
import argparse
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from reelvault import captions, ig, library, session
from reelvault.jobs import JobManager
from reelvault.store import Store

HOME = os.path.expanduser("~")
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_ROOT, "web")
ROOT = os.path.join(HOME, ".reelvault")
DATA = os.path.join(ROOT, "data")
JAR = os.path.join(ROOT, "cookies.txt")
DB = os.path.join(ROOT, "reelvault.db")

for d in (ROOT, DATA):
    os.makedirs(d, exist_ok=True)

STORE = Store(DB)
JOBS = JobManager()
USER_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _reap_orphaned_jobs():
    """A restart kills in-flight work; a 'running' row left behind makes every
    later job for that account fail with 409."""
    try:
        for j in STORE.running_jobs():
            STORE.update_job(j["id"], status="interrupted", finished_at=time.time())
    except Exception:
        pass


_reap_orphaned_jobs()



# --------------------------------------------------------------------------- utils
def ffmpeg_bin():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for base in (os.path.expanduser("~/.hermes/tools"), "/usr/local/bin", "/opt/homebrew/bin"):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            cand = os.path.join(base, name, "ffmpeg")
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    return None


def subprocess_env():
    env = dict(os.environ)
    ff = ffmpeg_bin()
    if ff:
        env["PATH"] = os.path.dirname(ff) + os.pathsep + env.get("PATH", "")
    return env


def lib_dir(username):
    # A NUL or a path separator here used to crash the request thread with
    # "embedded null byte"; reject anything that is not a plain handle.
    if not USER_RE.match(username or ""):
        raise ValueError("invalid account name")
    return os.path.join(DATA, username)


def archive_path(username):
    return os.path.join(ROOT, "archives", "%s.sqlite3" % username)


def thumb_path(username, shortcode):
    return os.path.join(lib_dir(username), ".thumbs", "%s.jpg" % shortcode)


def ensure_thumb(username, shortcode, media_file):
    tp = thumb_path(username, shortcode)
    if os.path.exists(tp):
        return tp
    ff = ffmpeg_bin()
    if not ff:
        return None
    os.makedirs(os.path.dirname(tp), exist_ok=True)
    tmp = tp + ".%d.tmp.jpg" % threading.get_ident()
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-ss", "0.5", "-i", media_file,
                        "-frames:v", "1", "-vf", "scale=270:-2", "-q:v", "6", tmp],
                       capture_output=True, timeout=40)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, tp)
            return tp
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return None


# Bounded pool: a library of 1,000 reels must not spawn 1,000 ffmpegs at once.
THUMBS = ThreadPoolExecutor(max_workers=4)
_THUMB_LOCK = threading.Lock()
_THUMB_PENDING = set()


def queue_thumb(username, shortcode, media_file):
    key = (username, shortcode)
    with _THUMB_LOCK:
        if key in _THUMB_PENDING:
            return False
        _THUMB_PENDING.add(key)

    def work():
        try:
            ensure_thumb(username, shortcode, media_file)
        finally:
            with _THUMB_LOCK:
                _THUMB_PENDING.discard(key)

    THUMBS.submit(work)
    return True


def backfill_thumbs(username):
    """Generate every missing thumbnail for an account, 4 at a time."""
    d = lib_dir(username)
    if not os.path.isdir(d):
        return 0
    queued = 0
    for path in sorted(glob_mp4(d)):
        sc = library.shortcode_of(path)
        if not os.path.exists(thumb_path(username, sc)):
            if queue_thumb(username, sc, path):
                queued += 1
    return queued


def glob_mp4(d):
    try:
        return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".mp4")]
    except OSError:
        return []


def postprocess(username):
    """Flatten gallery-dl's nested dirs, title every file, warm the thumbnails."""
    d = lib_dir(username)
    try:
        library.flatten(d)
        library.prune_playlist_meta(d)
        renamed = library.title_all(d)
    except Exception:
        renamed = 0
    threading.Thread(target=backfill_thumbs, args=(username,), daemon=True).start()
    return renamed


# --------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "ReelVault/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---- primitives ----
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        if body is None:
            body = b""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def file(self, path, ctype=None, download=False):
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        extra = {"Accept-Ranges": "bytes"}
        if download:
            extra["Content-Disposition"] = 'attachment; filename="%s"' % os.path.basename(path)
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                start = int(m.group(1) or 0)
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                if start > end:
                    return self._send(416, "range", "text/plain")
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Content-Length", str(end - start + 1))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    # ---- routing ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query)

        if p in ("/", "/index.html"):
            return self.file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self.file(os.path.join(WEB_DIR, os.path.basename(p)), None)
        if p == "/api/state":
            return self.api_state()
        if p == "/api/session/status":
            return self.api_session_status()
        m = re.match(r"^/api/accounts/(\d+)/posts$", p)
        if m:
            return self.api_posts(int(m.group(1)), q)
        m = re.match(r"^/api/accounts/(\d+)/posted$", p)
        if m:
            return self.api_posted(int(m.group(1)))
        m = re.match(r"^/api/accounts/(\d+)/index\.csv$", p)
        if m:
            return self.api_csv(int(m.group(1)))
        m = re.match(r"^/api/jobs/(\d+)/events$", p)
        if m:
            return self.sse(int(m.group(1)))
        m = re.match(r"^/media/([^/]+)/(.+)$", p)
        if m:
            return self.media(m.group(1), m.group(2))
        m = re.match(r"^/thumb/([^/]+)/([^/]+)\.jpg$", p)
        if m:
            return self.thumb(m.group(1), m.group(2))
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        if p == "/api/accounts":
            return self.api_add_account()
        if p == "/api/session/import":
            return self.api_session_import()
        if p == "/api/reveal":
            return self.api_reveal()
        m = re.match(r"^/api/accounts/(\d+)/post$", p)
        if m:
            return self.api_post(int(m.group(1)))
        m = re.match(r"^/api/accounts/(\d+)/preview$", p)
        if m:
            return self.api_preview(int(m.group(1)))
        m = re.match(r"^/api/accounts/(\d+)/scrape$", p)
        if m:
            return self.api_scrape(int(m.group(1)))
        m = re.match(r"^/api/accounts/(\d+)/retitle$", p)
        if m:
            return self.api_retitle(int(m.group(1)))
        m = re.match(r"^/api/accounts/(\d+)/thumbs$", p)
        if m:
            return self.api_thumbs(int(m.group(1)))
        m = re.match(r"^/api/jobs/(\d+)/cancel$", p)
        if m:
            return self.api_cancel(int(m.group(1)))
        return self._send(404, "not found", "text/plain")

    def do_DELETE(self):
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        m = re.match(r"^/api/accounts/(\d+)$", p)
        if m:
            return self.api_delete(int(m.group(1)))
        return self._send(404, "not found", "text/plain")

    # ---- api ----
    def api_state(self):
        gd_ok, gd_ver = ig.have_gallery_dl()
        yt_ok, yt_ver = ig.have_ytdlp()
        st = ig.session_state(JAR)
        accounts = []
        for a in STORE.accounts():
            d = lib_dir(a["username"])
            items = library.library(d) if os.path.isdir(d) else []
            job = STORE.active_job(a["id"])
            accounts.append({
                "id": a["id"], "username": a["username"],
                "kind": a.get("kind") or "instagram",
                "target": a.get("target") or a["username"],
                "total_posts": a["total_posts"], "quality": a["quality"],
                "saved": len(items),
                "size_bytes": sum(i["bytes"] for i in items),
                "job": job,
            })
        return self.json({
            "accounts": accounts,
            "deps": {"gallery_dl": {"ok": gd_ok, "version": gd_ver},
                     "yt_dlp": {"ok": yt_ok, "version": yt_ver},
                     "ffmpeg": bool(ffmpeg_bin())},
            "session": st,
            "caption": {"template": captions.DEFAULT_TEMPLATE,
                        "limit": captions.MAX_CAPTION},
        })

    def api_session_status(self):
        st = ig.session_state(JAR)
        return self.json(dict(st, present=os.path.exists(JAR)))

    def api_session_import(self):
        """Copy the browser's Instagram cookies, then re-clone the Firefox
        profile so the poster picks up the same (fresh) login."""
        body = self.body()
        try:
            browser, profile, keys = session.import_jar(JAR)
        except Exception as e:
            return self.json({"ok": False, "error": str(e)}, 400)
        ig._STATE.update(at=0.0, value=None)
        refreshed, note = False, ""
        try:
            from reelvault.poster import Poster
            posting = [j for j in JOBS.jobs.values()
                       if j.mode == "post" and j.status == "running"]
            if posting:
                note = "a post is running — profile clone left alone"
            else:
                Poster.refresh_profile()
                refreshed = True
        except Exception as e:
            note = "cookie import ok, profile clone skipped: %s" % str(e)[:120]
        return self.json({"ok": True, "browser": browser, "profile": profile,
                          "keys": keys, "profile_refreshed": refreshed, "note": note})

    def api_add_account(self):
        body = self.body()
        username = (body.get("source") or body.get("username") or "").strip()
        username = username.lstrip("@/").rstrip("/").split("/")[-1]
        if not USER_RE.match(username):
            return self.json({"ok": False, "error": "invalid username"}, 400)
        if not ig.logged_in(JAR):
            return self.json({"ok": False, "error": "not_connected"}, 409)
        total = ig.profile_total(JAR, username)   # 0 = unknown, not fatal
        acc = STORE.add_account(username, total)
        os.makedirs(lib_dir(username), exist_ok=True)
        os.makedirs(os.path.dirname(archive_path(username)), exist_ok=True)
        return self.json({"ok": True, "account": {"id": acc["id"], "username": username,
                                                  "total_posts": total}})

    def api_delete(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        job = STORE.active_job(aid)
        if job:
            j = JOBS.get(job["id"])
            if j:
                j.cancel()
        STORE.remove_account(aid)
        body = self.body()
        if body.get("delete_files"):
            shutil.rmtree(lib_dir(acc["username"]), ignore_errors=True)
            try:
                os.remove(archive_path(acc["username"]))
            except OSError:
                pass
        return self.json({"ok": True})

    def api_scrape(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        if STORE.active_job(aid):
            return self.json({"ok": False, "error": "a scrape is already running"}, 409)
        if not ig.logged_in(JAR):
            return self.json({"ok": False, "error": "not_connected"}, 409)
        body = self.body()
        limit_n = int(body.get("limit") or 0)
        quality = body.get("quality") or acc["quality"] or "720p"
        STORE.set_quality(aid, quality)
        if not ig.logged_in(JAR):
            return self.json({"ok": False, "error": "not_connected"}, 409)
        total = acc["total_posts"] or ig.profile_total(JAR, acc["username"])
        expected = min(limit_n, total) if (limit_n and total) else (limit_n or total)
        jid = STORE.new_job(aid, expected, limit_n, quality)
        env = subprocess_env()
        ff = ffmpeg_bin()
        if ff:
            env["REELVAULT_FFMPEG"] = ff
        cmd = ig.gallery_dl_cmd(JAR, acc["username"], lib_dir(acc["username"]),
                                archive_path(acc["username"]), quality, limit_n)
        job = JOBS.create(jid=jid, account=acc["username"], dest=lib_dir(acc["username"]),
                          cmd=cmd, expected=expected, limit_n=limit_n, quality=quality,
                          subprocess_env=env)
        job.start()

        def finalize():
            job._thread.join()
            try:
                STORE.update_job(jid, status=job.status,
                                 finished_at=job.finished or time.time(),
                                 downloaded=job.downloaded)
            except Exception:
                pass
            if job.downloaded:
                postprocess(acc["username"])
        threading.Thread(target=finalize, daemon=True).start()
        return self.json({"ok": True, "job": job.as_dict()})

    def api_cancel(self, jid):
        job = JOBS.get(jid)
        db = STORE.job(jid)
        if not job and db and db["status"] == "running":
            STORE.update_job(jid, status="cancelled", finished_at=time.time())
            return self.json({"ok": True, "note": "orphaned job marked cancelled"})
        if not job:
            return self.json({"ok": False, "error": "no such job"}, 404)
        job.cancel()
        STORE.update_job(jid, status="cancelled", finished_at=time.time())
        return self.json({"ok": True})

    def api_posted(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        return self.json({"ok": True, "posted": STORE.posted_for(aid)})

    def api_post(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        live = STORE.active_job(aid)
        if live:
            j = JOBS.get(live["id"])
            if j and j.status == "running":
                return self.json({"ok": False, "error": "a job is already running for this account"}, 409)
            # the process that owned this row is gone (server restart, crash)
            STORE.update_job(live["id"], status="interrupted", finished_at=time.time())

        body = self.body()
        d = lib_dir(acc["username"])
        have = {i["file"] for i in library.library(d)}
        files = body.get("files")
        if files:
            files = [f for f in files if isinstance(f, str) and f in have]
        else:
            posted = STORE.posted_for(aid)
            files = [f for f in sorted(have)
                     if posted.get(f, {}).get("status") != "posted"]

        if not files:
            return self.json({"ok": False, "error": "nothing to post"}, 400)

        publish = bool(body.get("publish"))
        template = (body.get("template") or "").strip()
        tags = (body.get("tags") or "").strip()
        result_path = os.path.join(ROOT, "post-result-%d.json" % int(time.time()))
        cmd = [sys.executable, "-m", "reelvault.post_cli",
               "--account", acc["username"], "--dir", d,
               "--files", json.dumps(files), "--result", result_path]
        if publish:
            cmd.append("--publish")
        if template:
            cmd += ["--template", template]
        if tags:
            cmd += ["--tags", tags]
        if body.get("allow_unknown_account"):
            cmd.append("--allow-unknown-account")

        jid = STORE.new_job(aid, len(files), len(files), "post")
        job = JOBS.create(jid=jid, account=acc["username"], dest=d, cmd=cmd,
                          expected=len(files), limit_n=len(files), quality="post",
                          subprocess_env=subprocess_env(), mode="post", cwd=APP_ROOT)
        job.start()

        def finalize():
            job._thread.join()
            try:
                STORE.update_job(jid, status=job.status, finished_at=job.finished or time.time(),
                                 downloaded=job.downloaded)
            except Exception:
                pass
            try:
                with open(result_path) as f:
                    results = json.load(f)
            except Exception:
                results = []
            for r in results:
                try:
                    STORE.record_post(aid, r.get("file", ""), r.get("status", "failed"),
                                      r.get("caption", ""), r.get("account", ""),
                                      r.get("error", ""))
                except Exception:
                    pass
            try:
                os.remove(result_path)
            except OSError:
                pass
        threading.Thread(target=finalize, daemon=True).start()
        return self.json({"ok": True, "job": job.as_dict(), "publish": publish,
                          "queued": len(files)})

    def api_preview(self, aid):
        """Show exactly what would be published — no browser, no upload."""
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        body = self.body()
        d = lib_dir(acc["username"])
        have = [i["file"] for i in library.library(d)]
        have_set = set(have)
        files = body.get("files")
        if not files:
            posted = STORE.posted_for(aid)
            files = [f for f in have if posted.get(f, {}).get("status") != "posted"]
        files = [f for f in files if isinstance(f, str) and f in have_set]
        files = files[:int(body.get("limit") or 5)]
        template = (body.get("template") or "").strip()
        tags = (body.get("tags") or "").strip()
        items = []
        for f in files:
            meta = library._meta_for(os.path.join(d, f))
            cap, info = captions.build(meta, template, tags, source=acc["username"])
            items.append({"file": f, "caption": cap, "info": info,
                          "original": (meta.get("description") or "").strip()[:2000]})
        return self.json({"ok": True, "items": items, "count": len(items),
                          "unposted": len([f for f in have
                                           if STORE.posted_for(aid).get(f, {}).get("status") != "posted"]),
                          "template": template or captions.DEFAULT_TEMPLATE})

    def api_posts(self, aid, q):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        try:
            items = library.library(lib_dir(acc["username"]))
        except (ValueError, OSError):
            return self.json({"ok": False, "error": "bad account name"}, 400)
        offset = int((q.get("offset") or ["0"])[0])
        limit = int((q.get("limit") or ["60"])[0])
        return self.json({"ok": True, "total": len(items),
                          "items": items[offset:offset + limit]})

    def api_csv(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self._send(404, "no such account", "text/plain")
        items = library.library(lib_dir(acc["username"]))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "likes", "shortcode", "title", "hashtags", "caption", "file", "url"])
        for i in items:
            w.writerow([i["date"], i["likes"], i["shortcode"], i["title"],
                        " ".join("#" + h for h in i["hashtags"]),
                        (i["caption"] or "").replace("\n", " "), i["file"], i["url"]])
        data = buf.getvalue().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s-index.csv"' % acc["username"])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def api_retitle(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        n = postprocess(acc["username"])
        return self.json({"ok": True, "renamed": n})

    def api_thumbs(self, aid):
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        return self.json({"ok": True, "queued": backfill_thumbs(acc["username"])})

    def api_reveal(self):
        body = self.body()
        aid = int(body.get("account_id") or 0)
        acc = STORE.account_by_id(aid)
        if not acc:
            return self.json({"ok": False, "error": "no such account"}, 404)
        d = lib_dir(acc["username"])
        if sys.platform == "darwin":
            subprocess.Popen(["open", d])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", d])
        else:
            os.startfile(d)  # noqa
        return self.json({"ok": True, "path": d})

    # ---- media ----
    def media(self, username, filename):
        if not USER_RE.match(username):
            return self._send(400, "bad user", "text/plain")
        base = lib_dir(username)
        path = os.path.abspath(os.path.join(base, os.path.basename(filename)))
        if not path.startswith(os.path.abspath(base) + os.sep) or not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        return self.file(path)

    def thumb(self, username, shortcode):
        if not USER_RE.match(username):
            return self._send(400, "bad user", "text/plain")
        tp = thumb_path(username, shortcode)
        if not os.path.exists(tp):
            base = lib_dir(username)
            try:
                cands = [f for f in os.listdir(base) if f.endswith(".mp4") and shortcode in f]
            except OSError:
                cands = []
            if cands:
                # generate in the background pool; the page re-renders and picks it up
                queue_thumb(username, shortcode, os.path.join(base, cands[0]))
            return self._send(404, "generating", "text/plain")
        return self.file(tp, "image/jpeg")

    # ---- sse ----
    def sse(self, jid):
        job = JOBS.get(jid)
        if not job:
            return self.json({"ok": False, "error": "no such job"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        q = job.subscribe()
        try:
            self.wfile.write(("data: %s\n\n" % json.dumps(
                {"type": "state", "job": job.as_dict()})).encode())
            self.wfile.flush()
            last_state = 0
            while True:
                try:
                    entry = q.get(timeout=1.0)
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "log", "entry": entry})).encode())
                    self.wfile.flush()
                except Exception:
                    pass
                if time.time() - last_state > 1.0:
                    last_state = time.time()
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "state", "job": job.as_dict()})).encode())
                    self.wfile.flush()
                if job.status != "running" and q.empty():
                    self.wfile.write(("data: %s\n\n" % json.dumps(
                        {"type": "end", "job": job.as_dict()})).encode())
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            job.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser(description="ReelVault — local Instagram reel archiver")
    ap.add_argument("--port", type=int, default=int(os.environ.get("REELVAULT_PORT", 8791)))
    ap.add_argument("--open", action="store_true", help="open the UI in your browser")
    args = ap.parse_args()

    ok, ver = ig.have_gallery_dl()
    if not ok:
        print("! gallery-dl not found. Install it with:\n    python3 -m pip install --user gallery-dl")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d" % args.port

    # Warm thumbnails for existing libraries without blocking startup.
    def warm():
        for a in STORE.accounts():
            try:
                backfill_thumbs(a["username"])
            except Exception:
                pass
    threading.Thread(target=warm, daemon=True).start()

    print("ReelVault running at %s  (data: %s)" % (url, ROOT))
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
