"""Thin SQLite store: accounts and scrape jobs. Library contents are read from
disk each time, so the files stay the source of truth."""
import os
import sqlite3
import time


class Store(object):
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL,
            total_posts INTEGER DEFAULT 0,
            quality TEXT DEFAULT '720p',
            last_run REAL
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
        """)
        self.db.commit()

    # ---- accounts ----
    def add_account(self, username, total=0):
        cur = self.db.execute(
            "INSERT OR IGNORE INTO accounts (username, created_at, total_posts) VALUES (?,?,?)",
            (username, time.time(), total))
        if cur.rowcount and total:
            pass
        elif total:
            self.db.execute("UPDATE accounts SET total_posts=? WHERE username=?", (total, username))
        self.db.commit()
        return self.account(username)

    def account(self, username):
        r = self.db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None

    def account_by_id(self, aid):
        r = self.db.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def accounts(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM accounts ORDER BY username").fetchall()]

    def remove_account(self, aid):
        self.db.execute("DELETE FROM jobs WHERE account_id=?", (aid,))
        self.db.execute("DELETE FROM accounts WHERE id=?", (aid,))
        self.db.commit()

    def set_total(self, aid, total):
        self.db.execute("UPDATE accounts SET total_posts=?, last_run=? WHERE id=?",
                        (total, time.time(), aid))
        self.db.commit()

    def set_quality(self, aid, quality):
        self.db.execute("UPDATE accounts SET quality=? WHERE id=?", (quality, aid))
        self.db.commit()

    # ---- jobs ----
    def new_job(self, account_id, expected, limit_n, quality):
        cur = self.db.execute(
            "INSERT INTO jobs (account_id, status, started_at, expected, limit_n, quality) "
            "VALUES (?,?,?,?,?,?)",
            (account_id, "running", time.time(), expected, limit_n, quality))
        self.db.commit()
        return cur.lastrowid

    def job(self, jid):
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None

    def jobs_for(self, account_id, limit=10):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM jobs WHERE account_id=? ORDER BY id DESC LIMIT ?",
            (account_id, limit)).fetchall()]

    def active_job(self, account_id):
        r = self.db.execute(
            "SELECT * FROM jobs WHERE account_id=? AND status='running' ORDER BY id DESC LIMIT 1",
            (account_id,)).fetchone()
        return dict(r) if r else None

    def update_job(self, jid, **kw):
        keys = ",".join("%s=?" % k for k in kw)
        self.db.execute("UPDATE jobs SET %s WHERE id=?" % keys,
                        tuple(kw.values()) + (jid,))
        self.db.commit()
