"""The site's feed of recently changed files (https://lolroms.com/rss.xml), used to skip re-reading
ticked folders that haven't changed before a sync.

The feed lists the most recently modified files (about 100-200, often less than a day's worth):
each <item> has a ?file= link and a pubDate. It shows no sizes, no deletions and no renamed folders,
so it is only a shortcut:

- A ticked folder (a "root") may use it only if all of its changes since Rom Sync last knew its
  contents are in the feed: the feed's oldest item must be no newer than that moment. Otherwise
  (the feed overflowed, or the folder was never read) the folder is re-read in full, as without it.
- A root is still re-read in full at least once every FULL_EVERY seconds, which catches what the
  feed doesn't report (deleted files, renamed folders, files uploaded with an old date).
- Within a root the feed vouches for, only the folders with changes newer than what is known about
  it are re-read, one page each.

Times are kept in the setting `feed_state`:
  {"site_read": epoch,                      start of the last finished full site scan
   "roots": {root: {"read": e, "known": e}}} last full re-read / last moment its contents were known
All stored times are moved back by MARGIN to allow for clocks that disagree.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from .catalog import ancestors

FEED_PATH = "rss.xml"
FULL_EVERY = 24 * 3600          # re-read every ticked folder in full at least this often
MARGIN = 600                    # seconds; allows for the site's clock and ours disagreeing


def _epoch(text: Optional[str]) -> Optional[float]:
    try:
        return parsedate_to_datetime((text or "").strip()).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def parse_feed(body: bytes) -> tuple:
    """-> ([(folder on the site, epoch)], build time or None). ValueError if it isn't a usable feed."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ValueError(f"not a feed ({e})") from None
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not an RSS feed")
    items = []
    for it in channel.findall("item"):
        when = _epoch(it.findtext("pubDate"))
        link = it.findtext("link") or it.findtext("guid") or ""
        path = next((v for k, v in parse_qsl(urlsplit(link.strip()).query) if k == "file"), None)
        if when is None or not path:
            continue
        path = path.strip("/")
        items.append((path.rsplit("/", 1)[0] if "/" in path else "", when))
    if not items:
        raise ValueError("the feed lists no files")
    return items, _epoch(channel.findtext("lastBuildDate"))


def _covers(root: str, folder: str) -> bool:
    return not root or folder == root or folder.startswith(root + "/")


def plan(roots: list, state: dict, items: list, built: Optional[float], now: float) -> dict:
    """Which ticked roots must be re-read in full, which the feed vouches for, and which folders
    inside those to re-read. -> {full, covered, folders, reasons: {root: why}, known}"""
    oldest = min(t for _, t in items)
    site_read = state.get("site_read")
    per_root = state.get("roots") or {}
    full, covered, reasons, since = [], [], {}, {}
    for r in roots:
        reads, knows = [site_read], [site_read]
        for a in (r, *ancestors(r)) if r else (r,):
            rec = per_root.get(a) or {}
            reads.append(rec.get("read"))
            knows.append(rec.get("known"))
        read = max((t for t in reads if t), default=None)
        known = max((t for t in knows if t), default=None)
        if read is None:
            full.append(r)
            reasons[r] = "not read since it was ticked"
        elif now - read > FULL_EVERY:
            full.append(r)
            reasons[r] = "a full re-read is due (at least once a day)"
        elif oldest > max(read, known or read):
            full.append(r)
            reasons[r] = "the list of recent changes doesn't reach back far enough"
        else:
            covered.append(r)
            since[r] = max(read, known or read)
    # only changes newer than what is already known about that folder
    folders = sorted({d for d, t in items for r in covered if _covers(r, d) and t >= since[r]})
    return {"full": full, "covered": covered, "folders": folders, "reasons": reasons,
            "known": min(built or now, now) - MARGIN}


def updated_state(state: dict, roots_now: list, full_read: list, full_started: float,
                  covered: list, known: float) -> dict:
    """The state after a refresh; only the roots ticked now are kept."""
    per_root = dict(state.get("roots") or {})
    for r in full_read:
        per_root[r] = {"read": full_started - MARGIN, "known": full_started - MARGIN}
    for r in covered:
        rec = dict(per_root.get(r) or {})
        rec["known"] = max(rec.get("known") or 0, known)
        per_root[r] = rec
    keep = set(roots_now)
    out = {k: v for k, v in state.items() if k != "roots"}
    out["roots"] = {k: v for k, v in per_root.items() if k in keep}
    return out


def now() -> float:
    return time.time()
