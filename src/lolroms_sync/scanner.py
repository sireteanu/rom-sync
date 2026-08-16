"""Reads the site's listing pages (never the files) into the catalog.

A full scan starts at the site root; a folder scan starts at ?dir=<folder> and stays below it.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional
from urllib.parse import parse_qsl, quote_plus, urlsplit
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


def url_for(base: str, path: str) -> str:
    base = base.rstrip("/") + "/"
    return base + (f"?dir={quote_plus(path)}" if path else "")


class Scanner:
    def __init__(self, catalog: Catalog, base_url: str, client: httpx.AsyncClient, *,
                 workers: int = 8, delay: float = 0.1, retries: int = 2, timeout: float = 15.0,
                 user_agent: str = "", robots: Optional[RobotFileParser] = None):
        self.catalog = catalog
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

    async def run(self, root: str = "") -> dict:
        """Scan the whole site (root="") or one folder and everything below it."""
        self.root, self.pages, self.files, self.errors = root, 0, 0, 0
        self.started = time.monotonic()
        start = url_for(self.base, root)
        cfg = Config(start_url=start, keep_params=frozenset({"dir"}), workers=self.workers,
                     delay=self.delay, user_agent=self.user_agent)
        rules = UrlRules(cfg)
        extractor = LinkExtractor(cfg, rules)
        self.lim = Limiter(self.workers, self.delay, True, 4.0)
        self.queue = asyncio.Queue()
        kind = "full" if not root else "folder"
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
                self.errors += 1
                self.catalog.page_failed(path, str(e))
                log.warning("could not read %s (%s)", path or "the site root", e)
                return
            page = rules.canonical(final) or url
            if body is None or not rules.in_scope(page):
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
                rel = rules.rel_path(furl)
                rows.append((furl, rel.rsplit("/", 1)[-1], rel, info))
            new = [l for l in links if l not in seen]
            seen.update(new)
            self.catalog.add_dirs({dir_of(l) for l in links if dir_of(l)}, scan_id)
            self.catalog.page_scanned(path, page, scan_id, rows)
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
                    if not self.stopping:
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
        status = "stopped" if self.stopping else "done"
        removed = (0, 0)
        if status == "done":
            removed = self.catalog.prune(scan_id, root)
        self.catalog.finish_scan(scan_id, self.pages, self.files, status)
        summary = {"root": root, "pages": self.pages, "files": self.files, "errors": self.errors,
                   "removed_files": removed[0], "removed_dirs": removed[1], "status": status}
        log.info("Listing scan %s: %d pages, %d files%s%s", status, self.pages, self.files,
                 f", {self.errors} pages failed" if self.errors else "",
                 f", {removed[0]} files / {removed[1]} folders no longer on the site" if any(removed) else "")
        return summary
