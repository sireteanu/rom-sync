"""Clean up: find files in the download folder that are no longer part of the sync, and delete
the ones the user confirmed.

A file on disk is a leftover when
  * "gone"     - the site doesn't list it any more (its folder was read without errors, or the
                 folder itself is gone and a full scan has finished since);
  * "unticked" - the site still lists it, but its folder isn't ticked (or its type isn't synced);
  * "part"     - an unfinished download (.part) of a file that isn't wanted any more.
Files Rom Sync can't judge (folders it never read, folders that failed to load) are never listed.
Nothing is deleted without an explicit list of paths, and only paths that are still leftovers
when the delete runs are removed. Symlinks are never followed or deleted; the manifest and
other dot-files are ignored.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .core.sync import MANIFEST_NAME, Manifest
from .core.util import fmt_bytes, fs_path, safe_segment

log = logging.getLogger("dircrawl")

REASONS = {
    "gone": "No longer on the site",
    "unticked": "Not ticked any more",
    "part": "Unfinished download that isn't needed",
}
IGNORED = {"thumbs.db", "desktop.ini"}


def walk(root: str) -> list:
    """[(rel dir, name, size)] for every regular file under root (runs in a thread)."""
    out = []
    root = fs_path(root)
    if not os.path.isdir(root):
        return out

    def scan(path: str, rel: str):
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name.lower() in IGNORED:
                continue
            try:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    scan(e.path, f"{rel}/{e.name}" if rel else e.name)
                elif e.is_file():
                    out.append((rel, e.name, e.stat().st_size))
            except OSError:
                continue

    scan(root, "")
    return out


def sanitized(path: str) -> str:
    return "/".join(safe_segment(s) for s in path.split("/")) if path else ""


def find(catalog, types, disk: list) -> list:
    """Judge the files found on disk -> [{path, size, reason}] (on the event loop thread)."""
    tree = catalog.tree(types)
    rules = catalog.rules()
    full = catalog.last_scan("full")
    full_done = bool(full and full.get("status") == "done")
    by_disk = {sanitized(p): p for p in tree}            # folder on disk -> catalog folder
    listed = catalog.files_by_rel({by_disk[d] for d, _, _ in disk if d in by_disk})

    # both answers depend only on the folder, so work them out once per folder, not once per file
    readable_cache: dict = {}
    ticked_cache: dict = {}

    def readable(disk_dir: str) -> bool:
        if disk_dir not in readable_cache:
            readable_cache[disk_dir] = _readable(disk_dir)
        return readable_cache[disk_dir]

    def _readable(disk_dir: str) -> bool:
        """Can we tell that a file in this folder isn't on the site? Its nearest known folder must
        have been read without errors (for the site root: a finished full scan)."""
        d = disk_dir
        while True:
            path = by_disk.get(d)
            if path is not None:
                node = tree[path]
                if node.error:
                    return False
                if path == "":
                    return full_done
                return bool(node.scanned)            # its listing shows what's still there
            if not d:
                return full_done
            d = d.rsplit("/", 1)[0] if "/" in d else ""

    def wanted(rel: str) -> Optional[bool]:
        hit = listed.get(rel)
        if hit is None:
            return None
        _url, d, ext = hit
        if d not in ticked_cache:
            ticked_cache[d] = bool(catalog.effective_files(d, rules))
        return ticked_cache[d] and (types is None or ext in types)

    out = []
    for d, name, size in disk:
        rel = f"{d}/{name}" if d else name
        if not d and name.startswith(MANIFEST_NAME):
            continue
        if name.endswith(".part"):
            w = wanted(rel[:-5])
            if w:
                continue                                   # still wanted: the next sync resumes it
            if w is None and not readable(d):
                continue
            out.append({"path": rel, "size": size, "reason": "part"})
            continue
        w = wanted(rel)
        if w:
            continue
        if w is False:
            out.append({"path": rel, "size": size, "reason": "unticked", "url": listed[rel][0]})
        elif readable(d):
            out.append({"path": rel, "size": size, "reason": "gone"})
    out.sort(key=lambda f: f["path"].casefold())
    return out


def summary(files: list) -> dict:
    by = {k: {"files": 0, "bytes": 0} for k in REASONS}
    for f in files:
        by[f["reason"]]["files"] += 1
        by[f["reason"]]["bytes"] += f["size"]
    return {"files": len(files), "bytes": sum(f["size"] for f in files), "by_reason": by}


def delete(root: str, files: list) -> dict:
    """Delete these leftovers (runs in a thread) and remove folders left empty by it.
    -> {deleted: [paths], bytes, errors: [text]}"""
    res = {"deleted": [], "bytes": 0, "errors": []}
    root_abs = os.path.abspath(root)
    dirs = set()
    for f in files:
        rel = f["path"]
        path = os.path.abspath(os.path.join(root_abs, *rel.split("/")))
        if not path.startswith(root_abs + os.sep) or os.path.islink(path):
            res["errors"].append(f"{rel}: not inside the download folder")
            continue
        try:
            os.remove(fs_path(path))
            res["deleted"].append(rel)
            res["bytes"] += f["size"]
            dirs.add(os.path.dirname(path))
        except FileNotFoundError:
            res["deleted"].append(rel)
        except OSError as e:
            res["errors"].append(f"{rel}: {e.strerror or e}")
    # folders that are empty now (deepest first), never the download folder itself
    for d in sorted(dirs, key=len, reverse=True):
        while d.startswith(root_abs + os.sep):
            try:
                os.rmdir(fs_path(d))
            except OSError:
                break
            d = os.path.dirname(d)
    if res["deleted"]:
        m = Manifest(root)
        try:
            m.forget(res["deleted"])
        finally:
            m.close()
    return res


def describe(res: dict) -> str:
    out = f"Clean up: deleted {len(res['deleted'])} files ({fmt_bytes(res['bytes'])})"
    if res["errors"]:
        out += f"; {len(res['errors'])} could not be deleted: " + "; ".join(res["errors"][:5])
    return out
