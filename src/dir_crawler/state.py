"""SQLite progress store used for --resume."""
from __future__ import annotations

import os
import sqlite3


class State:
    QUEUED, DONE, FAILED, SKIPPED = 0, 1, 2, 3

    def __init__(self, path: str, fresh: bool):
        if fresh:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS meta  (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS urls  (url TEXT PRIMARY KEY, depth INTEGER,
                                              state INTEGER DEFAULT 0, error TEXT);
            CREATE TABLE IF NOT EXISTS found (url TEXT PRIMARY KEY, depth INTEGER,
                                              files INTEGER, exts TEXT);
            CREATE TABLE IF NOT EXISTS files (url TEXT PRIMARY KEY, path TEXT, dir TEXT, ext TEXT,
                                              size INTEGER, state INTEGER DEFAULT 0, error TEXT);
        """)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(files)")}
        for col in ("listing_date", "listing_size", "note"):        # added in 3.2
            if col not in cols:
                self.db.execute(f"ALTER TABLE files ADD COLUMN {col} TEXT")

    @staticmethod
    def peek(path: str):
        """(start_url, pages done, pages pending, files pending) of a state file, or None."""
        if not os.path.exists(path):
            return None
        try:
            db = sqlite3.connect(path)
            row = db.execute("SELECT v FROM meta WHERE k='start_url'").fetchone()
            done = db.execute("SELECT COUNT(*) FROM urls WHERE state=1").fetchone()[0]
            pend = db.execute("SELECT COUNT(*) FROM urls WHERE state IN (0,2)").fetchone()[0]
            try:
                fpend = db.execute("SELECT COUNT(*) FROM files WHERE state IN (0,2)").fetchone()[0]
            except sqlite3.Error:
                fpend = 0
            db.close()
            return (row[0] if row else None), done, pend, fpend
        except sqlite3.Error:
            return None

    def get_meta(self, k):
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_meta(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

    def add_urls(self, rows):
        self.db.executemany("INSERT OR IGNORE INTO urls(url, depth) VALUES (?,?)", rows)

    def mark(self, url, state, error=None):
        self.db.execute("UPDATE urls SET state=?, error=? WHERE url=?", (state, error, url))

    def add_found(self, url, depth, files, exts):
        self.db.execute("INSERT OR REPLACE INTO found VALUES (?,?,?,?)", (url, depth, files, exts))

    def add_file(self, url, path, d, info, state=0, note=None):
        self.db.execute("INSERT OR IGNORE INTO files(url, path, dir, ext, listing_date, listing_size, "
                        "state, note) VALUES (?,?,?,?,?,?,?,?)",
                        (url, path, d, info.ext, info.date, info.size, state, note))

    def mark_file(self, url, state, size=None, error=None, note=None):
        self.db.execute("UPDATE files SET state=?, size=COALESCE(?, size), error=?, "
                        "note=COALESCE(?, note) WHERE url=?", (state, size, error, note, url))

    def pending(self):
        return self.db.execute(
            "SELECT url, depth FROM urls WHERE state IN (0,2) ORDER BY depth, rowid").fetchall()

    def pending_files(self):
        return self.db.execute(
            "SELECT url, path, ext, listing_date, listing_size FROM files "
            "WHERE state IN (0,2) ORDER BY rowid").fetchall()

    def all_urls(self):
        return {r[0] for r in self.db.execute("SELECT url FROM urls")}

    def done_urls(self):
        return {r[0] for r in self.db.execute("SELECT url FROM urls WHERE state=1")}

    def file_paths(self):
        return self.db.execute("SELECT url, path FROM files").fetchall()

    def files(self):
        return self.db.execute(
            "SELECT url, path, dir, ext, size, state, error, note, listing_date, listing_size "
            "FROM files ORDER BY path").fetchall()

    def found(self):
        return self.db.execute("SELECT url, depth, files, exts FROM found").fetchall()

    def failed(self):
        return self.db.execute(
            "SELECT 'page', url, error FROM urls WHERE state=2 "
            "UNION ALL SELECT 'file', url, error FROM files WHERE state=2 ORDER BY 1, 2").fetchall()

    def count(self, where, table="urls"):
        return self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]

    def commit(self):
        self.db.commit()

    def close(self):
        try:
            self.db.commit()
            self.db.close()
        except sqlite3.Error:
            pass
