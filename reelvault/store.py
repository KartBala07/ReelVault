"""Thin SQLite store: accounts, scrape jobs and posted reels.

Library contents are read from disk each time, so the files stay the source of
truth.

Threading: the HTTP server is threaded and job finalisers run on their own
threads, so **every** call into this class is serialised on one RLock. Sharing a
single ``sqlite3.Connection`` without that lock segfaults inside libsqlite3
(process exit 139, "Python quit unexpectedly") within minutes: CPython releases
the GIL around ``sqlite3_step``, so two threads prepare/step the same handle and
corrupt its heap. The symptom is not a clean exception — it shows up as a random
`embedded null byte`, then a garbage row, then a SIGSEGV.
"""
import os
import sqlite3
import threading
import time


class Store(object):
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self._init()

    def _init(self):
        with self._lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                total_posts INTEGER DEFAULT 0,
                quality TEXT DEFAULT '720p',
                last_run REAL,
                kind TEXT DEFAULT 'instagram',
                target TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                expected INTEGER DEFAULT 0,
                downloaded INTEGER DEFAULT 0,
                limit_n INTEGER DEFAULT 0,
                quality TEXT DEFAULT '720p',
                message TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS posted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                file TEXT NOT NULL,
                status TEXT NOT NULL,
                caption TEXT DEFAULT '',
                posted_as TEXT DEFAULT '',
                posted_at REAL NOT NULL,
                error TEXT DEFAULT '',
                UNIQUE(account_id, file)
            );
            """)
            # migrate older databases that predate multi-source support
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(accounts)").fetchall()}
            if "kind" not in cols:
                self.db.execute("ALTER TABLE accounts ADD COLUMN kind TEXT DEFAULT 'instagram'")
            if "target" not in cols:
                self.db.execute("ALTER TABLE accounts ADD COLUMN target TEXT")
            self.db.commit()

    # ---- accounts ----
    def add_account(self, username, total=0, kind="instagram", target=None):
        with self._lock:
            self.db.execute(
                "INSERT OR IGNORE INTO accounts (username, created_at, total_posts, kind, target) "
                "VALUES (?,?,?,?,?)",
                (username, time.time(), total, kind, target or username))
            if total:
                self.db.execute("UPDATE accounts SET total_posts=?, kind=COALESCE(kind,?), target=? "
                                "WHERE username=?", (total, kind, target or username, username))
            self.db.commit()
        return self.account(username)

    def account(self, username):
        with self._lock:
            r = self.db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None

    def account_by_id(self, aid):
        with self._lock:
            r = self.db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def accounts(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM accounts ORDER BY username").fetchall()
        return [dict(r) for r in rows]

    def remove_account(self, aid):
        with self._lock:
            self.db.execute("DELETE FROM jobs WHERE account_id=?", (aid,))
            self.db.execute("DELETE FROM accounts WHERE id=?", (aid,))
            self.db.commit()

    def set_total(self, aid, total):
        with self._lock:
            self.db.execute("UPDATE accounts SET total_posts=?, last_run=? WHERE id=?",
                            (total, time.time(), aid))
            self.db.commit()

    def set_quality(self, aid, quality):
        with self._lock:
            self.db.execute("UPDATE accounts SET quality=? WHERE id=?", (quality, aid))
            self.db.commit()

    # ---- jobs ----
    def new_job(self, account_id, expected, limit_n, quality):
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO jobs (account_id, status, started_at, expected, limit_n, quality) "
                "VALUES (?,?,?,?,?,?)",
                (account_id, "running", time.time(), expected, limit_n, quality))
            self.db.commit()
            return cur.lastrowid

    def job(self, jid):
        with self._lock:
            r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

    def jobs_for(self, account_id, limit=10):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM jobs WHERE account_id=? ORDER BY id DESC LIMIT ?",
                (account_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def active_job(self, account_id):
        with self._lock:
            r = self.db.execute(
                "SELECT * FROM jobs WHERE account_id=? AND status='running' "
                "ORDER BY id DESC LIMIT 1", (account_id,)).fetchone()
        return dict(r) if r else None

    def running_jobs(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        return [dict(r) for r in rows]

    def update_job(self, jid, **kw):
        if not kw:
            return
        keys = ",".join("%s=?" % k for k in kw)
        with self._lock:
            self.db.execute("UPDATE jobs SET %s WHERE id=?" % keys,
                            tuple(kw.values()) + (jid,))
            self.db.commit()

    # ---- posted reels ----
    def record_post(self, account_id, file, status, caption="", posted_as="", error=""):
        with self._lock:
            self.db.execute(
                "INSERT INTO posted (account_id, file, status, caption, posted_as, posted_at, error) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, file) DO UPDATE SET status=excluded.status, "
                "caption=excluded.caption, posted_as=excluded.posted_as, "
                "posted_at=excluded.posted_at, error=excluded.error",
                (account_id, file, status, caption[:500], posted_as, time.time(), error[:300]))
            self.db.commit()

    def posted_for(self, account_id):
        with self._lock:
            rows = self.db.execute(
                "SELECT file, status, posted_as, posted_at, error FROM posted WHERE account_id=?",
                (account_id,)).fetchall()
        return {r["file"]: dict(r) for r in rows}

    def clear_posted(self, account_id, file=None):
        with self._lock:
            if file:
                self.db.execute("DELETE FROM posted WHERE account_id=? AND file=?",
                                (account_id, file))
            else:
                self.db.execute("DELETE FROM posted WHERE account_id=?", (account_id,))
            self.db.commit()
