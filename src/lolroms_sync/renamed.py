"""Folders the site renamed: move what was downloaded under the old name to the new one.

lolroms dropped the set date from its folder names in October 2026
("Acorn/Risc PC (Flux) (20230506-040449)" -> "Acorn/Risc PC (Flux)"), and has moved folders to a
newer date before. A folder without a Saved as of its own then gets a new name on disk too, and
without this step every file in it would be downloaded again into the new folder.

Before planning a sync, a folder in the download folder whose name ends in a set date is merged
into its plain-named sibling when
  - the catalog saves a site folder at that plain path (the site has it now), and
  - no site folder is saved at the dated path any more (the site no longer has it).
Files move only where nothing is in the way; anything left over stays for Clean up. The download
record (.dir_crawler.db) moves along, so the next sync recognises the files as its own.
"""
from __future__ import annotations

import logging
import os
import shutil

from .catalog import SET_DATE
from .core.sync import Manifest
from .core.util import fs_path, safe_segment

log = logging.getLogger("dircrawl")


def _join(*parts: str) -> str:
    return "/".join(p for p in parts if p)


def _move_records(manifest: Manifest, old: str, new: str) -> None:
    """Download records of everything under old (a folder) or of old itself (a file) -> new."""
    pre = old + "/"
    for path in [p for p in manifest.rows if p == old or p.startswith(pre)]:
        rec = manifest.rows.pop(path)
        manifest.db.execute("DELETE FROM files WHERE path=?", (path,))
        rec.path = new + path[len(old):]
        manifest._save(rec)


def _merge(root: str, old_rel: str, new_rel: str, manifest: Manifest) -> int:
    """Move old_rel's contents into new_rel -> files moved."""
    src = fs_path(os.path.join(root, *old_rel.split("/")))
    dst = fs_path(os.path.join(root, *new_rel.split("/")))
    if not os.path.lexists(dst):                      # nothing there yet: one rename moves it all
        n = sum(len(f) for _, _, f in os.walk(src))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        _move_records(manifest, old_rel, new_rel)
        return n
    n = 0
    for dirpath, _dirs, files in os.walk(src):
        sub = os.path.relpath(dirpath, src)
        sub = "" if sub == "." else sub.replace(os.sep, "/")
        for name in files:
            s = os.path.join(dirpath, name)
            d = os.path.join(dst, *sub.split("/"), name) if sub else os.path.join(dst, name)
            if os.path.lexists(d) or os.path.islink(s):
                continue                               # keep what's there; Clean up offers the rest
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.move(s, d)
            _move_records(manifest, _join(old_rel, sub, name), _join(new_rel, sub, name))
            n += 1
    for dirpath, _dirs, _files in os.walk(src, topdown=False):   # remove what's now empty
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return n


def merge_renamed_folders(root: str, disk_folders) -> list:
    """disk_folders: every folder (relative, "/"-separated) where the catalog now saves files.
    -> [(old folder, new folder, files moved)] (runs in a worker thread)."""
    root_fs = fs_path(root)
    if not os.path.isdir(root_fs):
        return []
    wanted = {d.casefold(): d for d in disk_folders if d}
    moves: list = []
    manifest = None
    try:
        for _ in range(8):                             # dated folders inside dated folders
            moved_any = False
            for dirpath, dirnames, _files in os.walk(root_fs):
                rel = os.path.relpath(dirpath, root_fs)
                rel = "" if rel == "." else rel.replace(os.sep, "/")
                for name in list(dirnames):
                    if name.startswith(".") or not SET_DATE.search(name):
                        continue
                    old_rel = _join(rel, name)
                    if old_rel.casefold() in wanted or os.path.islink(os.path.join(dirpath, name)):
                        continue                       # the site still has this dated folder
                    new_rel = wanted.get(_join(rel, safe_segment(SET_DATE.sub("", name).strip())).casefold())
                    if new_rel is None:
                        continue
                    if manifest is None:
                        manifest = Manifest(root)
                    try:
                        n = _merge(root, old_rel, new_rel, manifest)
                    except OSError as e:
                        log.warning("Could not move %s to %s: %s", old_rel, new_rel, e)
                        continue
                    manifest.commit()
                    moves.append((old_rel, new_rel, n))
                    moved_any = True
                    dirnames.remove(name)
            if not moved_any:
                break
    finally:
        if manifest is not None:
            manifest.close()
    for old_rel, new_rel, n in moves:
        log.info("Moved %d %s from %s to %s (the site renamed the folder)", n,
                 "file" if n == 1 else "files", old_rel, new_rel)
    return moves
