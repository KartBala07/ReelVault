"""Scrape job runner: one gallery-dl subprocess per job, with a shared log ring
buffer and subscriber queues for the SSE endpoint."""
import os
import queue
import subprocess
import threading
import time

from . import ig


class Job(object):
    def __init__(self, jid, account, dest, cmd, expected=0, limit_n=0, quality="720p",
                 subprocess_env=None, mode="download", cwd=None):
        self.id = jid
        self.mode = mode
        self.cwd = cwd
        self.account = account
        self.dest = dest
        self.cmd = cmd
        self.quality = quality
        self.limit_n = limit_n
        self.env = subprocess_env
        self.proc = None
        self.status = "running"
        self.blocked = ""
        self.started = time.time()
        self.finished = None
        self.downloaded = 0
        self.expected = expected
        self.lines = []
        self.subs = []
        self._lock = threading.Lock()
        self._thread = None

    # ---- log plumbing ----
    def emit(self, text, kind="log"):
        entry = {"t": time.time(), "kind": kind, "text": text}
        with self._lock:
            self.lines.append(entry)
            if len(self.lines) > 800:
                del self.lines[:200]
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(entry)
            except queue.Full:
                pass

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self.subs.append(q)
            history = list(self.lines[-200:])
        for h in history:
            q.put_nowait(h)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self.subs:
                self.subs.remove(q)

    # ---- lifecycle ----
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _count_files(self):
        n = 0
        for root, _dirs, files in os.walk(self.dest):
            n += sum(1 for f in files if f.endswith((".mp4", ".jpg", ".webp", ".png")))
        return n

    def _run(self):
        cmd = self.cmd
        self.emit("$ " + " ".join(cmd), "cmd")
        before = self._count_files()
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=self.env, cwd=self.cwd)
        except Exception as e:
            self.emit("failed to start gallery-dl: %s" % e, "error")
            self.status = "failed"
            self.finished = time.time()
            return

        def pump():
            for line in self.proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                if self.mode == "post" and line.startswith(("POSTED ", "DRYRUN ", "FAILED ")):
                    if not line.startswith("FAILED"):
                        self.downloaded += 1
                if line.startswith("BLOCKED "):
                    self.blocked = line[len("BLOCKED "):].strip()
                    self.emit(line, "error")
                elif line.startswith(("WARNING", "ERROR", "[download][error]")):
                    self.emit(line, "warn")
                else:
                    self.emit(line)

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        while self.proc.poll() is None:
            if self.mode != "post":
                self.downloaded = max(0, self._count_files() - before)
            time.sleep(1.5)
        self.proc.wait()
        t.join(timeout=5)
        if self.mode != "post":
            self.downloaded = max(0, self._count_files() - before)
        rc = self.proc.returncode
        if self.status == "cancelled":
            pass
        elif rc == 0:
            self.status = "done"
            self.emit("finished: %d item(s)" % self.downloaded, "done")
        else:
            self.status = "partial" if self.downloaded else "failed"
            self.emit("job exited with code %d" % rc, "error")
        self.finished = time.time()

    def cancel(self):
        self.status = "cancelled"
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            time.sleep(1)
            if self.proc.poll() is None:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.emit("cancelled by user", "warn")

    def as_dict(self):
        return {
            "id": self.id, "account": self.account, "status": self.status,
            "downloaded": self.downloaded, "expected": self.expected,
            "limit": self.limit_n, "quality": self.quality,
            "started": self.started, "finished": self.finished,
            "blocked": self.blocked,
        }


class JobManager(object):
    def __init__(self):
        self.jobs = {}
        self._lock = threading.Lock()

    def create(self, **kw):
        job = Job(**kw)
        with self._lock:
            self.jobs[job.id] = job
        return job

    def get(self, jid):
        with self._lock:
            return self.jobs.get(jid)
