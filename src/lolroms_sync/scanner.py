"""Reads the site's listing pages (never the files) into the catalog.

A full scan starts at the site root; a folder scan starts at ?dir=<folder> and stays below it.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Optional
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .core.config import Config
from .core.constants import MAX_BODY, RETRY_STATUSES, THROTTLE_STATUSES
from .core.extract import LinkExtractor
from .core.limits import Limiter, _retry_after
from .core.urls import UrlRules
from .core.util import FetchError

from .catalog import Catalog

log = logging.getLogger("dircrawl")


def dir_of(url: str) -> str:
    for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if k.lower() == "dir":
            return v.strip("/")
    return ""


# the listing's sort links name the folder actually shown: href="?dir=Acorn&amp;sort=name"
_SORT_DIR = re.compile(rb'href="[^"]*[?&]dir=([^"&#]*)&(?:amp;)?sort=', re.IGNORECASE)


def shown_dir(body: bytes) -> Optional[str]:
    """The folder a listing page shows, from its sort links; None if it has none.

    Since October 2026 lolroms answers a folder it doesn't have (an old name, e.g. one that lost
    its set date) with *another* folder's listing and no error, so a page must be checked against
    the folder that was asked for."""
    m = _SORT_DIR.search(body)
    if m is None:
        return None
    return unquote_plus(m.group(1).decode("utf-8", "replace")).strip("/")


def header_dirs(body: bytes, page_url: str, rules: UrlRules) -> set:
    """Top-level folders linked from anywhere on the root page, header bar included.

    lolroms links some sections (Arcade, Pokéhacks, Audio [FLAC], fixdats) only from its header
    bar, which the normal extractor skips as navigation."""
    from urllib.parse import urljoin
    from selectolax.lexbor import LexborHTMLParser
    out = set()
    for a in LexborHTMLParser(body).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if "dir=" not in href.lower():
            continue
        url = rules.canonical(urljoin(page_url, href))
        d = dir_of(url) if url else ""
        if d and "/" not in d and rules.in_scope(url):
            out.add(url)
    return out


def site_trouble(err: str) -> bool:
    """Is this failure the site not answering (timeouts, server errors, refused connections) rather
    than something about the page (404, not allowed)?"""
    return err == "timed out" or err.startswith("HTTP 5") or (not err.startswith("HTTP") and "Error" in err)


def _dur(secs: float) -> str:
    if secs >= 3600:
        h = secs / 3600
        return f"{h:g} hour" + ("" if h == 1 else "s")
    if secs >= 60:
        m = round(secs / 60)
        return f"{m} minute" + ("" if m == 1 else "s")
    return f"{round(secs)} seconds"


def url_for(base: str, path: str) -> str:
    base = base.rstrip("/") + "/"
    return base + (f"?dir={quote_plus(path)}" if path else "")


class Scanner:
    def __init__(self, catalog: Catalog, base_url: str, client: httpx.AsyncClient, *,
                 workers: int = 8, delay: float = 0.1, retries: int = 2, timeout: float = 15.0,
                 user_agent: str = "", robots: Optional[RobotFileParser] = None, folder_map=None,
                 site_wait: float = 1800.0, check_every: float = 30.0):
        self.catalog = catalog
        # the site not answering at all: wait up to site_wait seconds for it (0 = don't), asking
        # again after check_every seconds, then twice as long each time (at most 5 minutes)
        self.site_wait = site_wait
        self.check_every = check_every
        self.gave_up = False
        self._waiting: Optional[asyncio.Task] = None
        self._streak = 0                      # pages in a row that failed because of the site
        self._held: list = []                 # those pages: read again when the site is back
        self._last_err = ""
        self.folder_map = folder_map          # where files are saved (folder_map.FolderMap)
        self.base = base_url
        self.client = client
        self.workers = workers
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.robots = robots
        self.stopping = False
        # progress
        self.root = ""
        self.pages = 0
        self.files = 0
        self.errors = 0
        self.started = time.monotonic()
        self.queue: Optional[asyncio.Queue] = None
        self.lim: Optional[Limiter] = None
        self.current = ""

    def progress(self) -> dict:
        el = max(time.monotonic() - self.started, 1e-6)
        return {"root": self.root, "pages": self.pages, "files": self.files, "errors": self.errors,
                "queued": self.queue.qsize() if self.queue else 0, "rate": round(self.pages / el, 1),
                "parallel": self.lim.limit if self.lim else 0, "delay": round(self.lim.delay, 2) if self.lim else 0,
                "current": self.current}

    def stop(self):
        self.stopping = True

    async def _wait_for_site(self) -> bool:
        """The site stopped answering: wait for it (one check for all workers). False = gave up."""
        if self._waiting is None or self._waiting.done():
            self._waiting = asyncio.create_task(self._until_site_answers())
        return await asyncio.shield(self._waiting)

    async def _until_site_answers(self) -> bool:
        log.warning("Rom Sync can't reach the site (%s) - the scan waits up to %s for it. If the site "
                    "loads in your browser, see 'Rom Sync can't reach the site' in the user guide.",
                    self._last_err or "no answer", _dur(self.site_wait))
        t0 = time.monotonic()
        step = self.check_every
        while not self.stopping:
            left = self.site_wait - (time.monotonic() - t0)
            if left <= 0:
                break
            end = time.monotonic() + min(step, left)
            while not self.stopping and time.monotonic() < end:
                await asyncio.sleep(min(1.0, max(0.0, end - time.monotonic())))
            if self.stopping:
                break
            try:
                r = await self.client.get(self.base, timeout=self.timeout)
                if r.status_code < 500:
                    log.info("The site can be reached again after %s - the scan carries on",
                             _dur(time.monotonic() - t0))
                    self._streak = 0
                    return True
            except httpx.HTTPError:
                pass
            step = min(step * 2, 300.0)
        if not self.stopping:
            self.gave_up = True
            log.error("The site still can't be reached after %s - scan stopped; nothing in the catalog "
                      "was changed", _dur(self.site_wait))
        return False

    async def _fetch(self, url: str):
        err = "unknown error"
        for attempt in range(self.retries + 1):
            pause = None
            await self.lim.acquire()
            t0 = time.monotonic()
            try:
                async with self.client.stream("GET", url) as r:
                    status = r.status_code
                    if status == 200:
                        ctype = r.headers.get("content-type", "").lower()
                        body = None
                        if not ctype or "html" in ctype:
                            buf = bytearray()
                            async for chunk in r.aiter_bytes():
                                buf += chunk
                                if len(buf) > MAX_BODY:
                                    break
                            body = bytes(buf)
                        self.lim.ok(time.monotonic() - t0)
                        return str(r.url), body
                    if status not in RETRY_STATUSES:
                        raise FetchError(f"HTTP {status}")
                    err = f"HTTP {status}"
                    if status in THROTTLE_STATUSES:
                        self.lim.cut(err)
                        pause = _retry_after(r.headers.get("retry-after"))
            except httpx.TimeoutException:
                err = "timed out"
                self.lim.cut("timeout")
            except httpx.TransportError as e:
                err = f"{type(e).__name__}: {e}".rstrip(": ")
            finally:
                await self.lim.release()
            if attempt < self.retries:
                wait = pause if pause is not None else min(30.0, 2.0 ** attempt) * (0.75 + random.random() / 2)
                await asyncio.sleep(wait)
        raise FetchError(err)

    async def run(self, root: str = "", recurse: bool = True) -> dict:
        """Scan the whole site (root="") or one folder and everything below it.
        recurse=False reads only that folder's own listing (its files and subfolder names)."""
        self.root, self.pages, self.files, self.errors = root, 0, 0, 0
        self.read: set = set()                # folders whose listing was read in this run
        self.started = time.monotonic()
        start = url_for(self.base, root)
        cfg = Config(start_url=start, keep_params=frozenset({"dir"}), workers=self.workers,
                     delay=self.delay, user_agent=self.user_agent)
        rules = UrlRules(cfg)
        extractor = LinkExtractor(cfg, rules)
        self.lim = Limiter(self.workers, self.delay, True, 4.0)
        self.queue = asyncio.Queue()
        kind = "full" if not root and recurse else "folder"
        scan_id = self.catalog.start_scan(kind, root)
        log.info("Reading the site's listings%s", f" under {root}" if root else "")
        seen = {rules.start}
        self.queue.put_nowait(rules.start)

        async def process(url: str):
            path = dir_of(url)
            self.current = path
            if self.robots is not None and not self.robots.can_fetch(self.user_agent, url):
                return
            try:
                final, body = await self._fetch(url)
            except FetchError as e:
                err = str(e)
                if self.site_wait > 0 and site_trouble(err) and not self.stopping and not self.gave_up:
                    # the first page, or several in a row, or while already waiting: the site is
                    # probably down - wait for it, then read those pages again
                    self._streak += 1
                    self._held.append(url)
                    self._last_err = err
                    waiting = self._waiting is not None and not self._waiting.done()
                    if url == rules.start or self._streak >= 3 or waiting:
                        if await self._wait_for_site():
                            held, self._held = self._held, []
                            self.errors -= max(0, len(held) - 1)       # counted before the wait
                            for u in held:
                                self.queue.put_nowait(u)
                            return
                        if self.gave_up:
                            return
                self.errors += 1
                self.catalog.page_failed(path, err)
                log.warning("could not read %s (%s)", path or "the site root", e)
                return
            self._streak = 0
            self._held.clear()
            page = rules.canonical(final) or url
            if body is None or not rules.in_scope(page):
                return
            shown = shown_dir(body)
            if shown is not None and shown.casefold() != path.casefold():
                # the site showed another folder instead (it no longer has this one): treat it as
                # gone, so its files aren't dropped as "removed" and a renamed folder is looked for
                self.catalog.page_failed(path, "not on the site any more")
                log.info("%s isn't on the site any more (it shows %s instead)", path or "the site root",
                         shown or "the site root")
                return
            if len(body) > 300_000:
                links, files, _ = await asyncio.to_thread(extractor.extract, body, final)
            else:
                links, files, _ = extractor.extract(body, final)
            path = dir_of(page)
            if not path and not root:
                links = set(links) | header_dirs(body, final, rules)
            rows = []
            for furl, info in files.items():
                rel = rules.rel_path(furl, self.folder_map)
                rows.append((furl, rel.rsplit("/", 1)[-1], rel, info))
            new = [l for l in links if l not in seen]
            seen.update(new)
            self.catalog.add_dirs({dir_of(l) for l in links if dir_of(l)}, scan_id)
            self.catalog.page_scanned(path, page, scan_id, rows)
            self.read.add(path)
            if not recurse:
                new = []
            self.pages += 1
            self.files += len(rows)
            for l in new:
                self.queue.put_nowait(l)
            if self.pages % 50 == 0:
                self.catalog.commit()

        async def worker():
            while True:
                url = await self.queue.get()
                try:
                    if not self.stopping and not self.gave_up:
                        await process(url)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # one bad page must not stop the scan
                    self.errors += 1
                    log.error("unexpected error reading %s: %r", url, e)
                finally:
                    self.queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(self.workers)]
        try:
            await self.queue.join()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.catalog.commit()
        status = "stopped" if self.stopping else "site down" if self.gave_up else "done"
        removed, renamed = (0, 0), {}
        if status == "done":
            removed = self.catalog.prune(scan_id, root)
            renamed = self.catalog.follow_renames()
            for old, new in renamed.items():
                log.info("The site renamed %s to %s - your ticks moved along", old, new.rsplit("/", 1)[-1])
        self.catalog.finish_scan(scan_id, self.pages, self.files, status)
        summary = {"root": root, "pages": self.pages, "files": self.files, "errors": self.errors,
                   "removed_files": removed[0], "removed_dirs": removed[1], "status": status,
                   "renamed": renamed}
        log.info("Listing scan %s: %d pages, %d files%s%s", status, self.pages, self.files,
                 f", {self.errors} pages failed" if self.errors else "",
                 f", {removed[0]} files / {removed[1]} folders no longer on the site" if any(removed) else "")
        return summary
