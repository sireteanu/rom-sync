"""The catalog: everything the site lists (folders and files), the folder selection, the
settings, and what has been synced - one SQLite file in the config folder.

Folder keys are the site's `dir` values ("Atari/5200"); the site root is "".
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .core.sync import size_range
from .core.util import local_rel

SYNCED_WORDS = ("new", "updated", "resumed", "up to date", "already in folder")

DEFAULT_SETTINGS = {
    "types": "archives",            # archives | all | ".7z,.zip"
    "parallel": "auto",             # auto | number
    "parallel_max": 16,
    "max_rate": "",                 # "" = unlimited, e.g. "5M"
    "min_free": "2G",
    "file_mode": "777",             # permissions for downloaded files and new folders; "" = system default
    "refresh_before_sync": True,    # re-read the selected folders' listings before syncing
    "resume_interrupted": True,     # restart a sync that was cut off (crash, reboot, restart)
    "interrupted_job": "",          # set while a sync runs; cleared when it ends normally
    "check_remote": False,
    "outage_wait_hours": 12,         # how long a sync waits for the site when it's down
    "sync_every_hours": 0,          # 0 = off
    "full_scan_every_days": 7,      # 0 = off (only when sync schedule is on, or on its own)
    "data_dir": "",                 # set at start-up from --data unless changed in the UI
}


OWN = "/."          # selection key suffix: "Nintendo/Super Famicom/." = only the files directly in it


def parent_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def ancestors(path: str):
    """'a/b/c' -> 'a/b', 'a', ''"""
    while path:
        path = parent_of(path)
        yield path


def size_bytes(text: Optional[str]) -> int:
    """Best estimate of a listing size like '3.54 KB'."""
    rng = size_range(text) if text else None
    return (rng[0] + rng[1]) // 2 if rng else 0


@dataclass
class Node:
    path: str
    name: str
    parent: str
    depth: int
    own_files: int = 0
    own_bytes: int = 0
    own_newest: str = ""
    own_synced: int = 0
    own_synced_bytes: int = 0
    own_failed: int = 0
    scanned: Optional[float] = None
    error: Optional[str] = None
    children: list = field(default_factory=list)
    # recursive totals
    files: int = 0
    bytes: int = 0
    newest: str = ""
    synced: int = 0
    synced_bytes: int = 0
    failed: int = 0
    dirs: int = 0


class Catalog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS dirs (
                path TEXT PRIMARY KEY, parent TEXT, name TEXT, depth INTEGER,
                url TEXT, scanned REAL, seen_scan INTEGER, error TEXT);
            CREATE TABLE IF NOT EXISTS files (
                url TEXT PRIMARY KEY, dir TEXT, name TEXT, rel TEXT, ext TEXT, date TEXT,
                size TEXT, bytes INTEGER, seen_scan INTEGER,
                local TEXT, local_size INTEGER, synced REAL, error TEXT);
            CREATE INDEX IF NOT EXISTS files_dir ON files(dir);
            CREATE TABLE IF NOT EXISTS selection (path TEXT PRIMARY KEY, include INTEGER);
            CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, root TEXT, started REAL,
                finished REAL, pages INTEGER, files INTEGER, status TEXT);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, started REAL, finished REAL,
                summary TEXT);
            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL, day TEXT, job REAL, kind TEXT,
                path TEXT, size INTEGER, note TEXT);
            CREATE INDEX IF NOT EXISTS changes_day ON changes(day);
            CREATE INDEX IF NOT EXISTS changes_job ON changes(job, kind);
        """)
        self.db.execute("INSERT OR IGNORE INTO dirs(path, parent, name, depth) VALUES ('', NULL, '', 0)")
        self._drop_set_dates()
        self.db.commit()
        self._tree: Optional[dict] = None
        self._tree_types = None
        self._tree_at = 0.0
        self.dirty = True

    def _drop_set_dates(self) -> None:
        """Catalogs from before 1.3.7 saved files under folder names with the set's date stamp
        ("Archimedes (20231029-220453)"); point them at the folder name without it."""
        fixes = []
        for url, rel in self.db.execute("SELECT url, rel FROM files WHERE rel GLOB '*(*-*)*/*'"):
            folder, _, name = rel.rpartition("/")
            new = f"{local_rel(folder)}/{name}"
            if new != rel:
                fixes.append((new, url))
        if fixes:
            self.db.executemany("UPDATE files SET rel=?, local=NULL, local_size=NULL, synced=NULL "
                                "WHERE url=?", fixes)

    def invalidate(self) -> None:
        """Structural change (ticks, a finished scan, settings): rebuild the tree on next read."""
        self._tree = None
        self.dirty = True

    # ------------------------------------------------------------------ settings
    def settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        with self.lock:
            for k, v in self.db.execute("SELECT k, v FROM settings"):
                try:
                    out[k] = json.loads(v)
                except ValueError:
                    out[k] = v
        return out

    def save_settings(self, values: dict) -> None:
        with self.lock:
            for k, v in values.items():
                if k in DEFAULT_SETTINGS:
                    self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, json.dumps(v)))
            self.db.commit()
        self.invalidate()

    # ------------------------------------------------------------------ scanning
    def start_scan(self, kind: str, root: str) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO scans(kind, root, started, status) VALUES (?,?,?,?)",
                                  (kind, root, time.time(), "running"))
            self.db.commit()
            return cur.lastrowid

    def finish_scan(self, scan_id: int, pages: int, files: int, status: str) -> None:
        with self.lock:
            self.db.execute("UPDATE scans SET finished=?, pages=?, files=?, status=? WHERE id=?",
                            (time.time(), pages, files, status, scan_id))
            self.db.commit()
        self.invalidate()

    def last_scan(self, kind: Optional[str] = None):
        q = "SELECT id, kind, root, started, finished, pages, files, status FROM scans"
        q += " WHERE kind=?" if kind else ""
        q += " ORDER BY id DESC LIMIT 1"
        with self.lock:
            row = self.db.execute(q, (kind,) if kind else ()).fetchone()
        if not row:
            return None
        return dict(zip(("id", "kind", "root", "started", "finished", "pages", "files", "status"), row))

    def add_dirs(self, paths: Iterable[str], scan_id: int) -> None:
        rows = []
        for p in paths:
            rows.append((p, parent_of(p), p.rsplit("/", 1)[-1], p.count("/") + 1))
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO dirs(path, parent, name, depth) VALUES (?,?,?,?)", rows)
            self.db.executemany("UPDATE dirs SET seen_scan=? WHERE path=?", [(scan_id, r[0]) for r in rows])
        self.dirty = True

    def page_scanned(self, path: str, url: str, scan_id: int, files: list) -> None:
        """files: [(url, name, rel, FileInfo)]"""
        now = time.time()
        with self.lock:
            if path:
                self.db.execute("INSERT OR IGNORE INTO dirs(path, parent, name, depth) VALUES (?,?,?,?)",
                                (path, parent_of(path), path.rsplit("/", 1)[-1], path.count("/") + 1))
            self.db.execute("UPDATE dirs SET url=?, scanned=?, seen_scan=?, error=NULL WHERE path=?",
                            (url, now, scan_id, path))
            for furl, name, rel, info in files:
                self.db.execute(
                    "INSERT INTO files(url, dir, name, rel, ext, date, size, bytes, seen_scan) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET dir=excluded.dir, "
                    "name=excluded.name, rel=excluded.rel, ext=excluded.ext, date=excluded.date, "
                    "size=excluded.size, bytes=excluded.bytes, seen_scan=excluded.seen_scan",
                    (furl, path, name, rel, info.ext, info.date, info.size, size_bytes(info.size), scan_id))
        self.dirty = True

    def page_failed(self, path: str, error: str) -> None:
        with self.lock:
            self.db.execute("UPDATE dirs SET error=? WHERE path=?", (error, path))
        self.dirty = True

    def prune(self, scan_id: int, root: str) -> tuple:
        """After a completed scan: forget files and folders the site no longer lists - but only
        under folders whose page was read successfully in this scan."""
        like = (root + "/%") if root else "%"
        with self.lock:
            ok = {r[0] for r in self.db.execute(
                "SELECT path FROM dirs WHERE seen_scan=? AND scanned IS NOT NULL AND error IS NULL "
                "AND (path=? OR path LIKE ?)", (scan_id, root, like))}
            gone_files = [r[0] for r in self.db.execute(
                "SELECT url, dir FROM files WHERE (seen_scan IS NULL OR seen_scan<>?) "
                "AND (dir=? OR dir LIKE ?)", (scan_id, root, like)) if r[1] in ok]
            gone_dirs = [r[0] for r in self.db.execute(
                "SELECT path, parent FROM dirs WHERE path<>'' AND (seen_scan IS NULL OR seen_scan<>?) "
                "AND path LIKE ?", (scan_id, like)) if r[1] in ok]
            self.db.executemany("DELETE FROM files WHERE url=?", [(u,) for u in gone_files])
            for d in gone_dirs:
                self.db.execute("DELETE FROM dirs WHERE path=? OR path LIKE ?", (d, d + "/%"))
                self.db.execute("DELETE FROM files WHERE dir=? OR dir LIKE ?", (d, d + "/%"))
            self.db.commit()
        self.invalidate()
        return len(gone_files), len(gone_dirs)

    def commit(self):
        with self.lock:
            self.db.commit()

    # ------------------------------------------------------------------ files for syncing
    def files_under(self, paths: Iterable[str], types) -> list:
        """[(url, rel, dir, ext, date, size)] for files in the given folders (not recursive)."""
        out = []
        paths = list(paths)
        with self.lock:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                q = ("SELECT url, rel, dir, ext, date, size FROM files WHERE dir IN (%s)"
                     % ",".join("?" * len(chunk)))
                out += [r for r in self.db.execute(q, chunk) if types is None or r[3] in types]
        return out

    def set_local(self, url: str, word: Optional[str], size: Optional[int], error: Optional[str]) -> None:
        with self.lock:
            self.db.execute("UPDATE files SET local=?, local_size=COALESCE(?, local_size), synced=?, "
                            "error=? WHERE url=?", (word, size, time.time(), error, url))
        self.dirty = True

    def files_by_rel(self, dirs: Iterable[str]) -> dict:
        """{saved path: (url, dir, ext)} for the files listed in the given folders."""
        out = {}
        dirs = list(dirs)
        with self.lock:
            for i in range(0, len(dirs), 500):
                chunk = dirs[i:i + 500]
                q = "SELECT rel, url, dir, ext FROM files WHERE dir IN (%s)" % ",".join("?" * len(chunk))
                for rel, url, d, ext in self.db.execute(q, chunk):
                    out[rel] = (url, d, ext)
        return out

    def forget_local(self, urls: Iterable[str]) -> None:
        """These files were deleted from the download folder."""
        with self.lock:
            self.db.executemany("UPDATE files SET local=NULL, local_size=NULL, error=NULL WHERE url=?",
                                [(u,) for u in urls])
            self.db.commit()
        self.dirty = True
        self.invalidate()

    # ------------------------------------------------------------------ selection
    def rules(self) -> dict:
        with self.lock:
            return {p: bool(i) for p, i in self.db.execute("SELECT path, include FROM selection")}

    @staticmethod
    def effective(path: str, rules: dict) -> bool:
        if path in rules:
            return rules[path]
        for a in ancestors(path):
            if a in rules:
                return rules[a]
        return False

    @classmethod
    def effective_files(cls, path: str, rules: dict) -> bool:
        """Whether the files directly in `path` are selected: its "files in this folder" rule
        (key "<path>/.") if there is one, else the folder's own tick."""
        own = path + OWN
        return rules[own] if own in rules else cls.effective(path, rules)

    def select(self, path: str, include: bool) -> None:
        """Tick / untick a folder: applies to everything below it, including folders added later.
        "<folder>/." ticks only the files directly in that folder, not its subfolders."""
        if path.endswith(OWN):
            folder = path[:-len(OWN)]
            with self.lock:
                rules = self.rules()
                if include == self.effective(folder, rules):
                    self.db.execute("DELETE FROM selection WHERE path=?", (path,))
                else:
                    self.db.execute("INSERT OR REPLACE INTO selection VALUES (?,?)", (path, int(include)))
                self.db.commit()
            self.invalidate()
            self._fold(folder, include)
            return
        with self.lock:
            rules = self.rules()
            like = path + "/%" if path else "%"
            self.db.execute("DELETE FROM selection WHERE path LIKE ? AND path<>?", (like, path))
            inherited = self.effective(parent_of(path), rules) if path else False
            if include == inherited:
                self.db.execute("DELETE FROM selection WHERE path=?", (path,))
            else:
                self.db.execute("INSERT OR REPLACE INTO selection VALUES (?,?)", (path, int(include)))
            self.db.commit()
        self.invalidate()
        if "/" in path:                    # not for top-level folders: there is no "everything" box
            self._fold(parent_of(path), include)

    def _fold(self, folder: str, include: bool) -> None:
        """Ticking (or unticking) every part of a folder one by one - each subfolder, and its own
        files if it has any - means the folder is fully ticked: fold the rules into one rule on
        the folder so its box shows a tick, not "-"."""
        if not folder:
            return
        with self.lock:
            kids = [r[0] for r in self.db.execute("SELECT path FROM dirs WHERE parent=?", (folder,))]
            own = self.db.execute("SELECT 1 FROM files WHERE dir=? LIMIT 1", (folder,)).fetchone()
        rules = self.rules()
        want = "on" if include else "off"
        if kids and {self.state_of(k, rules) for k in kids} != {want}:
            return
        if own and self.effective_files(folder, rules) != include:
            return
        if (kids or own) and self.state_of(folder, rules) == "partial":
            self.select(folder, include)

    def clear_selection(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM selection")
            self.db.commit()
        self.invalidate()

    def state_of(self, path: str, rules: dict) -> str:
        """'on' | 'off' | 'partial' for the tri-state checkbox."""
        eff = self.effective(path, rules)
        prefix = path + "/" if path else ""
        for p, inc in rules.items():
            if p != path and p.startswith(prefix) and inc != eff:
                return "partial"
        return "on" if eff else "off"

    def selected_dirs(self) -> list:
        rules = self.rules()
        tree = self.tree()
        return [p for p in tree if self.effective_files(p, rules)]

    def selection_roots(self) -> list:
        """Smallest set of folders covering the selection (for refreshing their listings)."""
        sel = set(self.selected_dirs())
        return sorted(p for p in sel if not any(a in sel for a in ancestors(p)))

    # ------------------------------------------------------------------ tree with totals
    def tree(self, types=None, max_age: float = 2.0) -> dict:
        """{path: Node} with recursive totals, rebuilt when the catalog changed."""
        key = tuple(sorted(types)) if types is not None else None
        if (self._tree is not None and not self.dirty and key == self._tree_types) or \
                (self._tree is not None and key == self._tree_types and time.time() - self._tree_at < max_age):
            return self._tree
        with self.lock:
            nodes = {}
            for path, parent, name, depth, scanned, error in self.db.execute(
                    "SELECT path, parent, name, depth, scanned, error FROM dirs"):
                nodes[path] = Node(path, name or "", parent or "", depth or 0, scanned=scanned, error=error)
            ext_filter, args = "", []
            if types is not None:
                ext_filter = "WHERE ext IN (%s)" % ",".join("?" * len(types))
                args = list(types)
            words = ",".join("'%s'" % w for w in SYNCED_WORDS)
            for d, n, b, newest, sn, sb, fl in self.db.execute(
                    f"SELECT dir, COUNT(*), SUM(bytes), MAX(date), "
                    f"SUM(local IN ({words})), SUM(CASE WHEN local IN ({words}) THEN bytes ELSE 0 END), "
                    f"SUM(error IS NOT NULL AND local IS NULL) FROM files {ext_filter} GROUP BY dir", args):
                node = nodes.get(d)
                if node is None:
                    node = nodes[d] = Node(d, d.rsplit("/", 1)[-1], parent_of(d), d.count("/") + 1)
                node.own_files, node.own_bytes, node.own_newest = n, b or 0, newest or ""
                node.own_synced, node.own_synced_bytes, node.own_failed = sn or 0, sb or 0, fl or 0
        # link children (create missing ancestors) and roll totals up, deepest first
        for path in list(nodes):
            p = path
            while p:
                par = parent_of(p)
                if par not in nodes:
                    nodes[par] = Node(par, par.rsplit("/", 1)[-1], parent_of(par), par.count("/") + 1 if par else 0)
                p = par
        for node in nodes.values():
            if node.path:
                nodes[node.parent].children.append(node.path)
        for node in sorted(nodes.values(), key=lambda n: -n.depth):
            node.files += node.own_files
            node.bytes += node.own_bytes
            node.synced += node.own_synced
            node.synced_bytes += node.own_synced_bytes
            node.failed += node.own_failed
            node.newest = max(node.newest, node.own_newest)
            if node.path:
                par = nodes[node.parent]
                par.files += node.files
                par.bytes += node.bytes
                par.synced += node.synced
                par.synced_bytes += node.synced_bytes
                par.failed += node.failed
                par.newest = max(par.newest, node.newest)
                par.dirs += node.dirs + 1
        for node in nodes.values():
            node.children.sort(key=str.casefold)
        self._tree, self._tree_types, self._tree_at = nodes, key, time.time()
        self.dirty = False
        return nodes

    def search(self, q: str, limit: int = 100) -> list:
        q = q.strip()
        if not q:
            return []
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT path FROM dirs WHERE path<>'' AND path LIKE ? ORDER BY depth, path LIMIT ?",
                (f"%{q}%", limit))]

    def counts(self) -> dict:
        with self.lock:
            dirs = self.db.execute("SELECT COUNT(*) FROM dirs WHERE path<>''").fetchone()[0]
            files, total = self.db.execute("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM files").fetchone()
        return {"dirs": dirs, "files": files, "bytes": total}

    # ------------------------------------------------------------------ history
    def add_history(self, kind: str, started: float, summary: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO history(kind, started, finished, summary) VALUES (?,?,?,?)",
                            (kind, started, time.time(), json.dumps(summary)))
            self.db.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 50)")
            self.db.commit()

    def history(self, limit: int = 20) -> list:
        with self.lock:
            rows = self.db.execute("SELECT kind, started, finished, summary FROM history "
                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"kind": k, "started": s, "finished": f, "summary": json.loads(j)} for k, s, f, j in rows]

    # ------------------------------------------------------------------ change history
    # What happened to files in the download folder: new / updated / resumed / failed (by a sync,
    # `job` = the sync's start time) and deleted (by Clean up). Kept for CHANGES_KEEP_DAYS.
    CHANGES_KEEP_DAYS = 365

    def add_change(self, job: Optional[float], kind: str, path: str, size: Optional[int] = None,
                   note: Optional[str] = None, t: Optional[float] = None) -> None:
        t = t or time.time()
        with self.lock:
            self.db.execute("INSERT INTO changes(t, day, job, kind, path, size, note) VALUES (?,?,?,?,?,?,?)",
                            (t, time.strftime("%Y-%m-%d", time.localtime(t)), job, kind, path, size, note))

    def prune_changes(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM changes WHERE t < ?", (time.time() - self.CHANGES_KEEP_DAYS * 86400,))
            self.db.commit()

    def change_days(self, limit: int = 120) -> list:
        """[{day, counts: {kind: n}, bytes: {kind: b}}], newest day first."""
        with self.lock:
            days = [r[0] for r in self.db.execute(
                "SELECT DISTINCT day FROM changes ORDER BY day DESC LIMIT ?", (limit,))]
            out = {d: {"day": d, "counts": {}, "bytes": {}} for d in days}
            if days:
                q = ("SELECT day, kind, COUNT(*), SUM(COALESCE(size, 0)) FROM changes WHERE day IN (%s) "
                     "GROUP BY day, kind" % ",".join("?" * len(days)))
                for d, k, n, b in self.db.execute(q, days):
                    out[d]["counts"][k] = n
                    out[d]["bytes"][k] = b or 0
        return [out[d] for d in days]

    def changes(self, day: Optional[str] = None, job: Optional[float] = None, kind: Optional[str] = None,
                limit: int = 2000) -> tuple:
        """(total, [{t, kind, path, size, note}]) newest first."""
        where, args = [], []
        if day:
            where.append("day=?")
            args.append(day)
        if job is not None:
            where.append("job=?")
            args.append(job)
        if kind:
            where.append("kind=?")
            args.append(kind)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM changes" + w, args).fetchone()[0]
            rows = self.db.execute("SELECT t, kind, path, size, note FROM changes" + w +
                                   " ORDER BY t DESC, id DESC LIMIT ?", args + [limit]).fetchall()
        return total, [dict(zip(("t", "kind", "path", "size", "note"), r)) for r in rows]

    def unchanged_since(self, since: float, limit: int = 2000) -> tuple:
        """Files a sync found up to date (or adopted) since a time -> (total, rows)."""
        q = " FROM files WHERE synced >= ? AND local IN ('up to date', 'already in folder')"
        with self.lock:
            total = self.db.execute("SELECT COUNT(*)" + q, (since,)).fetchone()[0]
            rows = self.db.execute("SELECT synced, local, rel, bytes" + q + " ORDER BY rel COLLATE NOCASE LIMIT ?",
                                   (since, limit)).fetchall()
        return total, [{"t": t, "kind": "uptodate", "path": r, "size": b,
                        "note": "already in the folder" if w == "already in folder" else None}
                       for t, w, r, b in rows]

    def close(self):
        with self.lock:
            self.db.commit()
            self.db.close()
