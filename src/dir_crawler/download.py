"""Downloading one file into a synced folder - shared by the CLI crawler and the web app.

`Downloader.plan()` decides what a listed file needs (see sync.plan); files that need nothing are
handled right there without a request. `Downloader.download()` fetches the rest: .part files,
Range/If-Range resume, conditional GET, HTML-instead-of-file detection, archive header check,
disk-space guard, site date on the saved file, and feedback to the DownloadGate.
Results are reported through callbacks so each front end can keep its own bookkeeping.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import time
from typing import Callable, Optional

import httpx

from .config import Config
from .constants import CHUNK, MAGIC, RETRY_STATUSES, THROTTLE_STATUSES
from .limits import DownloadGate, RateLimit, _retry_after, _total_from_headers
from .sync import (ADOPT, CHECK, FETCH_ACTIONS, NEW, RESUME, UPDATE, FileInfo, Manifest, http_date,
                   http_date_epoch, plan, stamp_file)
from .util import DiskFull, FetchError, fmt_bytes, fs_path

log = logging.getLogger("dircrawl")

DONE_WORD = {NEW: "new", UPDATE: "updated", RESUME: "resumed", CHECK: "updated"}
DONE, QUEUED = "done", "queued"     # download() results (or a float: retry after that many seconds)
MAX_THROTTLE_RETRIES = 20          # push-back from the server doesn't use up a file's normal retries

# on_done(url, rel, word, size)   word: new | updated | resumed | up to date | already in folder
DoneCallback = Callable[[str, str, str, int], None]
# on_failed(url, rel, error)
FailCallback = Callable[[str, str, str], None]


class Downloader:
    def __init__(self, cfg: Config, client: httpx.AsyncClient, manifest: Manifest,
                 gate: Optional[DownloadGate], *, root: Optional[str] = None,
                 bucket: Optional[RateLimit] = None,
                 allowed: Optional[Callable[[str], bool]] = None,
                 on_done: Optional[DoneCallback] = None, on_failed: Optional[FailCallback] = None,
                 on_throttle: Optional[Callable[[str], None]] = None,
                 pending: Optional[Callable[[], int]] = None):
        self.cfg = cfg
        self.client = client
        self.manifest = manifest
        self.gate = gate
        self.root = root or cfg.download_dir
        self.bucket = bucket
        self.allowed = allowed or (lambda url: True)
        self.on_done = on_done or (lambda *a: None)
        self.on_failed = on_failed or (lambda *a: None)
        self.on_throttle = on_throttle or (lambda why: None)
        # files not finished yet *including* the one asking (queued, waiting for a slot, running)
        self.pending = pending or (lambda: self.in_progress)
        self.in_progress = 0
        self.retry_state: dict = {}      # url -> counters that survive being put back in the queue
        self.active: dict = {}           # relative path -> [received, total or None]
        self.bytes = 0                   # bytes received
        self.touched_dirs: set = set()   # folders whose files were saved or re-dated (stamp_folders)
        self.successes = 0
        self.last_success_started = 0.0  # start time of the most recent download that succeeded
        self.limit_streak = 0            # consecutive "web page instead of file" answers
        self.paused: Optional[str] = None
        self.stop_requested = False

    # ---- helpers ----
    def dest_of(self, rel: str) -> str:
        return fs_path(os.path.join(self.root, *rel.split("/")))

    def pause(self, reason: str) -> None:
        if not self.paused:
            self.paused = reason
            log.error("Downloads paused: %s. Everything not yet downloaded stays queued.", reason)

    def plan(self, url: str, rel: str, info: FileInfo):
        """-> (action, reason). For files that need no request (SKIP / ADOPT) the manifest is
        updated and on_done is called here, and action is returned as None."""
        dest = self.dest_of(rel)
        action, reason = plan(dest, self.manifest.get(rel), info,
                              check_remote=self.cfg.check_remote, overwrite=self.cfg.overwrite)
        if action in FETCH_ACTIONS:
            return action, reason
        size = os.path.getsize(dest)
        rec = self.manifest.get(rel)
        stamp_file(dest, info, rec.last_modified if rec else None)   # fix dates of files we already have
        self._set_mode(dest)
        if action == ADOPT:
            self.manifest.done(rel, url, size, None, None, info)
            self.on_done(url, rel, "already in folder", size)
        else:
            self.manifest.seen_unchanged(rel, info)
            self.on_done(url, rel, "up to date", size)
        return None, reason

    def _done(self, url, rel, word, size, got=0, t0=None):
        if word not in ("up to date", "already in folder"):
            self.successes += 1
            if t0:
                self.last_success_started = max(self.last_success_started, t0)
            self.limit_streak = 0
            speed = f"  ({fmt_bytes(got / max(time.monotonic() - t0, 1e-3))}/s)" if t0 and got else ""
            log.info("%-8s %s  %s%s", word, rel, fmt_bytes(size), speed)
        else:
            log.debug("%s: %s", word, rel)
        self.on_done(url, rel, word, size)

    def _failed(self, url, rel, err):
        log.warning("download failed: %s (%s)", rel, err)
        self.on_failed(url, rel, err)

    def _record_unchanged(self, rel, url, dest, info, lm, etag):
        if self.manifest.get(rel) is None:
            self.manifest.done(rel, url, os.path.getsize(dest), lm, etag, info)
        else:
            self.manifest.seen_unchanged(rel, info)

    async def _pushback(self, why: str):
        self.on_throttle(why)
        if self.gate:
            await self.gate.throttled(why)

    # ---- the download ----
    async def download(self, url: str, rel: str, info: FileInfo, action: str, reason: str = ""):
        """Fetch one file. Returns:
          DONE     it ended (downloaded, up to date or failed)
          QUEUED   left queued because downloads are paused or stopped
          a float  the server pushed back: put it back in the queue and retry after that many
                   seconds, *without* holding a download slot while waiting"""
        self.in_progress += 1
        try:
            result = await self._download(url, rel, info, action, reason)
        finally:
            self.in_progress -= 1
        if not isinstance(result, float):
            self.retry_state.pop(url, None)
        return result

    def _set_mode(self, path: str) -> None:
        """Apply cfg.file_mode (e.g. 0o777) to a saved file and remember its folder for
        stamp_folders(). Best effort: a share or a Windows drive may not support it, and that must
        never fail a download."""
        self.touched_dirs.add(os.path.dirname(path))
        if self.cfg.file_mode is None:
            return
        try:
            os.chmod(path, self.cfg.file_mode)
        except OSError as e:
            log.debug("could not set permissions on %s: %s", path, e)

    def _make_dirs(self, folder: str) -> None:
        """Create a file's folder; with cfg.file_mode, new folders (up to the download root) get it
        too, plus the 'enter' bit wherever reading is allowed, so they can be opened."""
        mode = self.cfg.file_mode
        if mode is None:
            os.makedirs(folder, exist_ok=True)
            return
        new = []
        root = os.path.abspath(self.root or "")
        f = os.path.abspath(folder)
        while f and not os.path.isdir(f) and f != root and os.path.dirname(f) != f:
            new.append(f)
            f = os.path.dirname(f)
        os.makedirs(folder, exist_ok=True)
        dir_mode = mode | ((mode & 0o444) >> 2)
        for d in reversed(new):
            try:
                os.chmod(d, dir_mode)
            except OSError as e:
                log.debug("could not set permissions on %s: %s", d, e)

    async def _download(self, url, rel, info, action, reason):
        cfg, man = self.cfg, self.manifest
        if not self.allowed(url):
            self._failed(url, rel, "robots.txt disallows it")
            return DONE
        dest = self.dest_of(rel)
        part = dest + ".part"
        self._make_dirs(os.path.dirname(dest))
        if action == RESUME and not os.path.exists(part) and os.path.exists(dest):
            os.replace(dest, part)                         # continue a truncated file
        if action == UPDATE and os.path.exists(part) and cfg.overwrite and url not in self.retry_state:
            os.remove(part)
        log.debug("%s: %s (%s)", action, rel, reason)
        ext = os.path.splitext(rel)[1].lower()
        rec = man.get(rel)
        err = "unknown error"
        # survives the file being put back in the queue between tries
        rs = self.retry_state.setdefault(url, {"throttles": 0, "html": 0, "first_html": None})
        attempts, attempt = max(cfg.retries, 3) + 1, 0

        while attempt < attempts:
            if self.paused or self.stop_requested:
                return QUEUED
            pause, pushback, slow_down, got, total = None, False, False, 0, None
            have = os.path.getsize(part) if os.path.exists(part) else 0
            conditional = action == CHECK and have == 0 and os.path.exists(dest)
            headers = {}
            if have:
                headers["Range"] = f"bytes={have}-"
                if rec and rec.part_validator:
                    headers["If-Range"] = rec.part_validator   # restart if the file changed meanwhile
            elif conditional:
                if rec and rec.etag:
                    headers["If-None-Match"] = rec.etag
                headers["If-Modified-Since"] = (rec.last_modified if rec and rec.last_modified
                                                else http_date(os.stat(dest).st_mtime))
            t0 = time.monotonic()
            try:
                async with self.client.stream("GET", url, headers=headers) as r:
                    status = r.status_code
                    lm, etag = r.headers.get("last-modified"), r.headers.get("etag")
                    if status == 304 and conditional:
                        self._record_unchanged(rel, url, dest, info, lm or (rec and rec.last_modified), etag)
                        self._done(url, rel, "up to date", os.path.getsize(dest))
                        return DONE
                    if status == 416 and have:              # the .part is already complete
                        os.replace(part, dest)
                        stamp_file(dest, info, lm)
                        self._set_mode(dest)
                        man.done(rel, url, have, lm, etag, info)
                        self._done(url, rel, DONE_WORD[action], have)
                        return DONE
                    if status in THROTTLE_STATUSES or status in RETRY_STATUSES:
                        err = f"HTTP {status}"
                        pushback = slow_down = status in THROTTLE_STATUSES
                        pause = _retry_after(r.headers.get("retry-after"))
                        raise FetchError(err)
                    if status not in (200, 206):
                        self._failed(url, rel, f"HTTP {status}")
                        return DONE
                    ctype = r.headers.get("content-type", "").lower()
                    if "text/html" in ctype:
                        body = (await r.aread())[:400].decode("utf-8", "replace")
                        title = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
                        hint = f' ("{title.group(1).strip()[:80]}")' if title else ""
                        rs["html"] += 1
                        if rs["first_html"] is None:
                            rs["first_html"] = time.monotonic()
                        others_active = len(self.active)     # other files transferring right now
                        if rs["html"] >= 2 and self.last_success_started > rs["first_html"]:
                            # a download that started after this page appeared worked:
                            # the site is fine, this file itself is the problem
                            self._failed(url, rel, f"server sends a web page instead of this file{hint}")
                            return DONE
                        if rs["html"] >= 2 and self.pending() <= 1 and not others_active:
                            # nothing else left to try - pausing wouldn't help (next sync retries it)
                            self._failed(url, rel, f"server sends a web page instead of this file{hint}")
                            return DONE
                        self.limit_streak += 1
                        if (self.limit_streak >= max(3, (self.gate.limit if self.gate else 1) + 2)
                                and not others_active and self.pending() > 1):
                            # nothing is getting through and other files are waiting: daily limit
                            self.pause(f"the site keeps sending a web page instead of files{hint} - "
                                       "probably its download limit")
                            return QUEUED
                        pushback = True                     # retry later; not a reason for fewer slots
                        err = f"server sent a web page instead of the file{hint}"
                        raise FetchError(err)
                    total = _total_from_headers(r.headers, status, 0 if status == 200 else have)
                    if conditional and status == 200:
                        # server ignored the conditional request - compare what it says
                        local = os.path.getsize(dest)
                        same_lm = lm and ((rec and rec.last_modified == lm) or
                                          (http_date_epoch(lm) or 0) <= os.stat(dest).st_mtime + 1)
                        same_tag = etag and rec and rec.etag == etag
                        if total == local and (same_lm or same_tag):
                            self._record_unchanged(rel, url, dest, info, lm, etag)
                            self._done(url, rel, "up to date", local)
                            return DONE
                    if status == 200:
                        have = 0                            # fresh start (or server ignored Range)
                        man.part_started(rel, url, etag or lm)
                        rec = man.get(rel)
                    if total:
                        free = shutil.disk_usage(os.path.dirname(dest)).free
                        if free - (total - have) < cfg.min_free:
                            raise DiskFull(f"{fmt_bytes(free)} free, {fmt_bytes(total - have)} needed "
                                           f"(keeping {fmt_bytes(cfg.min_free)} spare)")
                    self.active[rel] = [have, total]
                    with open(part, "ab" if status == 206 else "wb") as f:
                        async for chunk in r.aiter_bytes(CHUNK):
                            f.write(chunk)
                            n = len(chunk)
                            have += n
                            got += n
                            self.bytes += n
                            self.active[rel][0] = have
                            if self.bucket:
                                await self.bucket.consume(n)
                            if self.stop_requested:
                                raise FetchError("stopped")
                if total and have < total:
                    raise FetchError(f"connection dropped at {fmt_bytes(have)} of {fmt_bytes(total)}")
                if cfg.verify and ext in MAGIC and rel.lower().endswith(ext):
                    with open(part, "rb") as f:
                        head = f.read(8)
                    if not head.startswith(MAGIC[ext]):
                        os.remove(part)
                        self._failed(url, rel, f"not a valid {ext} file (removed)")
                        return DONE
                os.replace(part, dest)
                stamp_file(dest, info, lm)                  # file date = date shown on the website
                self._set_mode(dest)
                man.done(rel, url, have, lm, etag, info)
                self._done(url, rel, DONE_WORD[action], have, got, t0)
                return DONE
            except DiskFull as e:
                self.pause(f"not enough disk space ({e}); free up space or lower the free-space setting")
                return QUEUED
            except FetchError as e:
                err = str(e)
                if self.stop_requested:
                    return QUEUED
            except httpx.TimeoutException:
                err = "timed out"
                pushback = slow_down = got == 0
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
                err = f"{type(e).__name__}: {e}".rstrip(": ")
                pushback = slow_down = got == 0             # refused / dropped before any data
            except httpx.TransportError as e:
                err = f"{type(e).__name__}: {e}".rstrip(": ")
            finally:
                self.active.pop(rel, None)

            if slow_down:
                await self._pushback(err)
            if pushback and rs["throttles"] < MAX_THROTTLE_RETRIES:
                rs["throttles"] += 1                        # server push-back: doesn't use up retries
                wait = pause if pause is not None else random.uniform(1.0, 3.0) * min(rs["throttles"], 5)
                log.debug("pushed back (%s), %s goes back in the queue for %.1fs", err, rel, wait)
                return float(wait)
            attempt += 1
            if attempt < attempts:
                wait = pause if pause is not None else min(60.0, 2.0 ** attempt) * (0.75 + random.random() / 2)
                kept = os.path.getsize(part) if os.path.exists(part) else 0
                log.warning("download retry %d/%d in %.0fs (%s): %s%s", attempt, attempts - 1, wait,
                            err, rel, f" - resuming from {fmt_bytes(kept)}" if kept else "")
                await asyncio.sleep(wait)
        self._failed(url, rel, err)
        return DONE


async def run_item(downloader: "Downloader", gate: Optional[DownloadGate], queue: asyncio.Queue, item: tuple):
    """What a download worker does with one queue item (url, rel, info, action, reason[, not_before]):
    wait until it is due (without holding a slot), download it, and put it back if pushed back."""
    url, rel, info, action, reason = item[:5]
    not_before = item[5] if len(item) > 5 else 0.0
    if not_before:
        delay = not_before - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    if downloader.paused or downloader.stop_requested:
        return QUEUED
    if gate:
        await gate.acquire()
    try:
        if downloader.paused or downloader.stop_requested:
            return QUEUED
        result = await downloader.download(url, rel, info, action, reason)
    finally:
        if gate:
            await gate.release()
    if isinstance(result, float):
        queue.put_nowait((url, rel, info, action, reason, time.monotonic() + result))
    return result
