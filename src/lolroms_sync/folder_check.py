"""Check folder: compare the download folder with what the catalog thinks is synced.

Files can change on disk without Rom Sync knowing: deleted or moved by hand, copied in from
elsewhere, cut short. This reads the folder once (no network, nothing is downloaded, moved or
deleted) and corrects each catalog file's "synced" state, so the tree's Synced column and the
selection totals match the disk. The next sync then fetches whatever is missing or incomplete.
The manifest is only read: proper adoption (dates, permissions) stays the sync's job.
"""
from __future__ import annotations

from typing import Callable, Optional

from .catalog import SYNCED_WORDS
from .core.sync import size_matches


def compare(states: list, disk: dict, manifest_get: Callable[[str], Optional[object]]) -> dict:
    """states: Catalog.local_states() rows; disk: {rel: size} of the files in the download folder;
    manifest_get(rel) -> manifest Record or None.
    -> {found: [(url, word, size)], lost: [url], counts: {...}, missing: [rel], changed: [rel]}"""
    found, lost, missing, changed = [], [], [], []
    counts = {"checked": 0, "present": 0, "added": 0, "missing": 0, "changed": 0}
    for url, rel, date, size_text, local, local_size in states:
        if not rel:
            continue
        counts["checked"] += 1
        synced = local in SYNCED_WORDS
        size = disk.get(rel)
        if size is None:
            if synced:                                   # was there, isn't any more
                lost.append(url)
                missing.append(rel)
                counts["missing"] += 1
            continue
        rec = manifest_get(rel)
        if rec is not None and getattr(rec, "size", None) is not None:
            ok = size == rec.size                        # downloaded by Rom Sync: exact size known
        else:
            ok = size_matches(size, size_text) is not False   # put there by hand: listing size fits
        if not ok:
            if synced:
                lost.append(url)
                changed.append(rel)
                counts["changed"] += 1
            continue
        counts["present"] += 1
        if not synced:                                   # on disk, but the catalog didn't know
            found.append((url, "up to date" if rec is not None else "already in folder", size))
            counts["added"] += 1
        elif local_size != size:
            found.append((url, local, size))
    return {"found": found, "lost": lost, "counts": counts, "missing": missing, "changed": changed}


def describe(counts: dict) -> str:
    c = counts
    parts = [f"{c['present']:,} of {c['checked']:,} catalog files are in the download folder"]
    if c["added"]:
        parts.append(f"{c['added']:,} found that Rom Sync didn't know about")
    if c["missing"]:
        parts.append(f"{c['missing']:,} synced files are missing")
    if c["changed"]:
        parts.append(f"{c['changed']:,} have a different size than expected")
    if not (c["added"] or c["missing"] or c["changed"]):
        parts.append("nothing changed")
    text = "; ".join(parts)
    if c["missing"] or c["changed"]:
        text += ". Ticked ones are downloaded again at the next sync"
    return text
