"""The crawler: listing pages, file downloads (sync), live display and output files."""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import random
import sqlite3
import time
from collections import deque
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table

from . import __version__
from .config import Config
from .constants import MAX_BODY, RETRY_STATUSES, THROTTLE_STATUSES
from .download import Downloader, run_item
from .extract import LinkExtractor
from .limits import DownloadGate, Limiter, RateLimit, _retry_after
from .state import State
from .sync import CHECK, NEW, RESUME, UPDATE, FileInfo, Manifest
from .urls import UrlRules
from .util import FetchError, fmt_bytes

try:
    import h2  # noqa: F401  (enables HTTP/2 in httpx)
    HAVE_H2 = True
except ImportError:
    HAVE_H2 = False

log = logging.getLogger("dircrawl")


class Stats:
    def __init__(self):
        self.pages = 0
        self.dirs = 0
        self.files = 0
        self.errors = 0
        self.retries = 0
        self.requests = 0
        self.net_time = 0.0
        self.robots_skipped = 0
        self.started = time.monotonic()
        # downloads
        self.dl_found = 0
        self.dl_new = 0
        self.dl_updated = 0
        self.dl_resumed = 0
        self.dl_uptodate = 0
        self.dl_failed = 0
        self.downloader = None

    @property
    def dl_bytes(self) -> int:
        return self.downloader.bytes if self.downloader else 0

    @property
    def dl_done(self) -> int:
        return self.dl_new + self.dl_updated + self.dl_resumed


class Crawler:
    def __init__(self, cfg: Config, console: Console):
        self.cfg = cfg
        self.console = console
        self.rules = UrlRules(cfg)
        self.extractor = LinkExtractor(cfg, self.rules)
        self.state = State(cfg.state_path, fresh=not cfg.resume)
        self.manifest: Optional[Manifest] = Manifest(cfg.download_dir) if cfg.download_dir else None
        self.stats = Stats()
        self.queue: Optional[asyncio.Queue] = None
        self.dlq: Optional[asyncio.Queue] = None
        self.lim: Optional[Limiter] = None
        self.gate: Optional[DownloadGate] = None
        self.bucket: Optional[RateLimit] = None
        self.client: Optional[httpx.AsyncClient] = None
        self.robots: Optional[RobotFileParser] = None
        self.live: Optional[Live] = None
        self.seen: set = set()
        self.processed: set = set()
        self.files_seen: set = set()
        self.path_owner: dict = {}       # casefolded relative path -> file URL
        self.downloader: Optional[Downloader] = None
        self.dl_claimed = 0              # files taken from the queue and not finished yet
        self.speed = 0.0
        self._speed_samples: deque = deque(maxlen=6)
        self.stopping = False
        self.claimed = 0                 # pages started this run (for --max-pages)
        self.interactive = False
        self.track_files = bool(cfg.download_dir or cfg.list_files)
        self.gate_key = f"max_parallel:{self.rules.host}"

    # ---- helpers ----
    def label(self, url: str) -> str:
        return self.rules.name(url) or url

    def allowed(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(self.cfg.user_agent, url)

    def assign_path(self, url: str) -> str:
        """Relative save path, made unique (case-insensitively, for Windows/macOS)."""
        rel = self.rules.rel_path(url)
        owner = self.path_owner.get(rel.casefold())
        if owner and owner != url:
            stem, ext = os.path.splitext(rel)
            i = 2
            while f"{stem} ({i}){ext}".casefold() in self.path_owner:
                i += 1
            rel = f"{stem} ({i}){ext}"
        self.path_owner[rel.casefold()] = url
        return rel

    @property
    def active(self) -> dict:
        return self.downloader.active if self.downloader else {}

    @property
    def dl_paused(self) -> Optional[str]:
        return self.downloader.paused if self.downloader else None

    def _on_done(self, url, rel, word, size):
        st = self.stats
        if word in ("up to date", "already in folder"):
            st.dl_uptodate += 1
            self.state.mark_file(url, State.SKIPPED, size, note=word)
            return
        if word == "new":
            st.dl_new += 1
        elif word == "resumed":
            st.dl_resumed += 1
        else:
            st.dl_updated += 1
        self.state.mark_file(url, State.DONE, size, note=word)

    def _on_failed(self, url, rel, err):
        self.state.mark_file(url, State.FAILED, error=err)
        self.stats.dl_failed += 1

    def decide(self, url: str, rel: str, info: FileInfo):
        """Plan one file; files needing no request are handled right away. -> queue item or None."""
        action, reason = self.downloader.plan(url, rel, info)
        return (url, rel, info, action, reason) if action else None

    # ---- setup ----
    async def load_robots(self) -> None:
        p = urlsplit(self.rules.start)
        url = f"{p.scheme}://{p.netloc}/robots.txt"
        text = ""
        try:
            r = await self.client.get(url, timeout=10)
            if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                text = r.text
        except httpx.HTTPError:
            pass
        if not text:
            log.info("No robots.txt rules found")
            return
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        self.robots = rp
        cd = rp.crawl_delay(self.cfg.user_agent)
        if cd and float(cd) > self.lim.base_delay:
            self.lim.base_delay = self.lim.delay = float(cd)
            log.info("robots.txt asks for Crawl-delay %ss - applied", cd)

    def seed(self) -> None:
        st = self.state
        if self.cfg.resume and st.get_meta("start_url"):
            self.seen = st.all_urls()
            self.processed = st.done_urls()
            found = st.found()
            self.stats.pages = len(self.processed)
            self.stats.dirs = len(found)
            self.stats.files = sum(r[2] for r in found)
            pend = st.pending()
            for url, depth in pend:
                self.queue.put_nowait((url, depth))
            for url, path in st.file_paths():
                self.files_seen.add(url)
                self.path_owner[path.casefold()] = url
            self.stats.dl_found = len(self.files_seen)
            queued = 0
            if self.dlq is not None:
                for url, path, ext, date, size in st.pending_files():
                    item = self.decide(url, path, FileInfo(ext, date, size))
                    if item:
                        self.dlq.put_nowait(item)
                        queued += 1
            log.info("Resuming: %d pages done, %d directories with files, %d pages to go%s",
                     self.stats.pages, self.stats.dirs, len(pend),
                     f", {queued} files to download" if self.dlq is not None else "")
        else:
            st.set_meta("start_url", self.rules.start)
            st.set_meta("version", __version__)
            st.add_urls([(self.rules.start, 0)])
            self.seen = {self.rules.start}
            self.queue.put_nowait((self.rules.start, 0))
        st.commit()

    # ---- network: listing pages ----
    async def fetch(self, url: str):
        """-> (final_url, body or None when not HTML). Raises FetchError."""
        cfg = self.cfg
        err = "unknown error"
        for attempt in range(cfg.retries + 1):
            pause = None
            await self.lim.acquire()
            t0 = time.monotonic()
            try:
                async with self.client.stream("GET", url) as r:
                    status = r.status_code
                    if status == 200:
                        ctype = r.headers.get("content-type", "").lower()
                        body = None
                        if not ctype or "html" in ctype or "xml" in ctype:
                            buf = bytearray()
                            async for chunk in r.aiter_bytes():
                                buf += chunk
                                if len(buf) > MAX_BODY:
                                    log.warning("page larger than %d MB, truncated: %s",
                                                MAX_BODY // 1_000_000, self.label(url))
                                    break
                            body = bytes(buf)
                        took = time.monotonic() - t0
                        self.stats.requests += 1
                        self.stats.net_time += took
                        self.lim.ok(took)
                        return str(r.url), body
                    self.stats.requests += 1
                    self.stats.net_time += time.monotonic() - t0
                    if status not in RETRY_STATUSES:
                        raise FetchError(f"HTTP {status}")
                    err = f"HTTP {status}"
                    if status in THROTTLE_STATUSES:
                        self.lim.cut(err)
                        pause = _retry_after(r.headers.get("retry-after"))
            except httpx.TimeoutException:
                err = f"timed out after {cfg.timeout:.0f}s"
                self.lim.cut("timeout")
            except httpx.TransportError as e:
                err = f"{type(e).__name__}: {e}".rstrip(": ")
            finally:
                await self.lim.release()
            if attempt < cfg.retries:
                self.stats.retries += 1
                wait = pause if pause is not None else min(30.0, 2.0 ** attempt) * (0.75 + random.random() / 2)
                log.warning("retry %d/%d in %.1fs (%s): %s", attempt + 1, cfg.retries, wait, err,
                            self.label(url))
                await asyncio.sleep(wait)
        raise FetchError(err)

    # ---- per page ----
    async def process(self, url: str, depth: int) -> None:
        st = self.state
        if not self.allowed(url):
            self.stats.robots_skipped += 1
            st.mark(url, State.SKIPPED, "robots.txt")
            log.debug("robots.txt disallows %s", url)
            return
        t0 = time.monotonic()
        try:
            final, body = await self.fetch(url)
        except FetchError as e:
            self.stats.errors += 1
            st.mark(url, State.FAILED, str(e))
            log.warning("failed: %s (%s)", self.label(url), e)
            return
        page = self.rules.canonical(final) or url
        if body is None or page in self.processed or not self.rules.in_scope(page):
            st.mark(url, State.DONE)
            if body is None:
                log.debug("not an HTML page: %s", url)
            elif page != url:
                log.debug("redirected (%s -> %s), skipped", url, page)
            return

        if len(body) > 300_000:      # big listing: parse in a thread so downloads keep flowing
            links, files, skipped = await asyncio.to_thread(self.extractor.extract, body, final)
        else:
            links, files, skipped = self.extractor.extract(body, final)
        took = time.monotonic() - t0
        self.processed.add(page)
        self.processed.add(url)
        self.stats.pages += 1
        n = self.stats.pages

        exts = sorted({f.ext for f in files.values()})
        if files:
            st.add_found(page, depth, len(files), " ".join(exts))
            self.stats.dirs += 1
            self.stats.files += len(files)

        counts: dict = {}
        if self.track_files and files:
            types = self.cfg.dl_types
            name = self.label(page)
            for furl, info in files.items():
                if (types is not None and info.ext not in types) or furl in self.files_seen:
                    continue
                self.files_seen.add(furl)
                rel = self.assign_path(furl)
                st.add_file(furl, rel, name, info)
                self.stats.dl_found += 1
                if self.dlq is None:
                    continue
                item = self.decide(furl, rel, info)
                key = item[3] if item else "up to date"
                counts[key] = counts.get(key, 0) + 1
                if item:
                    self.dlq.put_nowait(item)

        new = []
        if self.cfg.max_depth is None or depth < self.cfg.max_depth:
            new = [l for l in links if l not in self.seen]
            self.seen.update(new)
            st.add_urls([(l, depth + 1) for l in new])
            for l in new:
                self.queue.put_nowait((l, depth + 1))
        st.mark(url, State.DONE)

        kids = self.rules.children(page, links)
        sub = f"  {kids} subfolders" if kids else ""
        if files:
            words = {NEW: "new", RESUME: "to resume", UPDATE: "changed", CHECK: "to check",
                     "up to date": "up to date"}
            plan_txt = ", ".join(f"{v} {words[k]}" for k, v in counts.items())
            log.info("[%d] %s  -  %d files (%s)%s%s  %.2fs", n, self.label(page), len(files),
                     ", ".join(exts), f"  [{plan_txt}]" if plan_txt else "", sub, took)
        else:
            log.info("[%d] %s%s  %.2fs", n, self.label(page), sub, took)
        if skipped:
            log.debug("    ignored %d header/nav/sortbar/back links", skipped)

    async def worker(self) -> None:
        while True:
            url, depth = await self.queue.get()
            try:
                if self.cfg.max_pages and self.claimed >= self.cfg.max_pages and not self.stopping:
                    self.stopping = True
                    log.warning("Reached --max-pages %d - stopping (use --resume to continue later)",
                                self.cfg.max_pages)
                if not self.stopping:
                    self.claimed += 1
                    await self.process(url, depth)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let one bad page kill a worker
                self.stats.errors += 1
                self.state.mark(url, State.FAILED, f"internal error: {e!r}")
                log.error("unexpected error on %s: %r", url, e)
            finally:
                self.queue.task_done()

    async def dl_worker(self) -> None:
        while True:
            item = await self.dlq.get()
            url, rel = item[0], item[1]
            self.dl_claimed += 1
            try:
                await run_item(self.downloader, self.gate, self.dlq, item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stats.dl_failed += 1
                self.state.mark_file(url, State.FAILED, error=f"internal error: {e!r}")
                log.error("unexpected error downloading %s: %r", rel, e)
            finally:
                self.dl_claimed -= 1
                self.dlq.task_done()

    async def ticker(self) -> None:
        last_save = last_status = last_second = time.monotonic()
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            if self.live is not None:
                self.live.update(self.status_panel(), refresh=True)
            if self.gate and not self.dl_paused:
                await self.gate.tick(self.dlq.qsize())
            if now - last_second >= 1:
                last_second = now
                self.state.commit()
                if self.manifest:
                    self.manifest.commit()
                self._speed_samples.append((now, self.stats.dl_bytes))
                t_old, b_old = self._speed_samples[0]
                if len(self._speed_samples) < 2:
                    t_old, b_old = self.stats.started, 0
                self.speed = (self.stats.dl_bytes - b_old) / (now - t_old) if now > t_old else 0.0
            if now - last_save >= 30:
                self.write_outputs(quiet=True)
                last_save = now
            if self.live is None and now - last_status >= 15:
                log.info("status: %s", self.status_line())
                last_status = now

    async def run(self) -> None:
        cfg = self.cfg
        self.queue = asyncio.Queue()
        self.dlq = asyncio.Queue() if cfg.download_dir else None
        self.lim = Limiter(cfg.workers, cfg.delay, cfg.adaptive, cfg.slow_secs)
        self.bucket = RateLimit(cfg.max_rate) if cfg.max_rate > 0 else None
        n_dl = 0
        if self.dlq is not None:
            known = self.manifest.get_meta(self.gate_key)
            known = int(known) if known and known.isdigit() else None
            self.gate = DownloadGate(auto=cfg.dl_auto, start=cfg.dl_workers, hard_max=cfg.dl_max,
                                     known_max=known, ramp_secs=cfg.dl_ramp_secs,
                                     probe_secs=cfg.dl_probe_secs, confirm_secs=cfg.dl_confirm_secs)
            n_dl = self.gate.hard_max if cfg.dl_auto else self.gate.limit
            if cfg.dl_auto:
                log.info("Parallel downloads: automatic, starting at %d%s (max %d)", self.gate.limit,
                         " - the server's limit from last time" if known else "", self.gate.hard_max)
        use_h2 = cfg.http2 and HAVE_H2
        conns = cfg.workers + n_dl
        async with httpx.AsyncClient(
                http2=use_h2, follow_redirects=True, max_redirects=8,
                timeout=httpx.Timeout(cfg.timeout, connect=min(cfg.timeout, 10.0)),
                limits=httpx.Limits(max_connections=conns, max_keepalive_connections=conns),
                headers={"User-Agent": cfg.user_agent,
                         "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"}) as client:
            self.client = client
            if not cfg.ignore_robots:
                await self.load_robots()
            if self.dlq is not None:
                self.downloader = Downloader(cfg, client, self.manifest, self.gate, bucket=self.bucket,
                                             allowed=self.allowed, on_done=self._on_done,
                                             on_failed=self._on_failed,
                                             pending=lambda: self.dlq.qsize() + self.dl_claimed)
                self.stats.downloader = self.downloader
            self.seed()
            if self.queue.empty() and (self.dlq is None or self.dlq.empty()):
                log.info("Nothing left to do.")
                return
            tasks = [asyncio.create_task(self.worker()) for _ in range(cfg.workers)]
            tasks += [asyncio.create_task(self.dl_worker()) for _ in range(n_dl)]
            tasks.append(asyncio.create_task(self.ticker()))
            try:
                await self.queue.join()
                if self.dlq is not None and not self.dlq.empty():
                    log.info("Crawl finished - %d downloads still to go", self.dlq.qsize())
                if self.dlq is not None:
                    await self.dlq.join()
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    # ---- display ----
    def status_line(self) -> str:
        s = self.stats
        el = time.monotonic() - s.started
        q = self.queue.qsize() if self.queue else 0
        avg = s.net_time / s.requests if s.requests else 0.0
        lim = self.lim
        line = (f"{s.pages} pages, {q} queued, {s.dirs} dirs with files, {s.files} files, "
                f"{s.errors} errors | {s.pages / max(el, 1e-6):.1f} pages/s, avg {avg:.2f}s, "
                f"parallel {lim.limit if lim else 0}/{self.cfg.workers}, "
                f"delay {lim.delay if lim else self.cfg.delay:.2f}s")
        if self.dlq is not None:
            g = self.gate
            line += (f" | downloads: {s.dl_new} new, {s.dl_updated} updated, {s.dl_resumed} resumed, "
                     f"{s.dl_uptodate} up to date, {s.dl_failed} failed, {self.dlq.qsize()} waiting, "
                     f"{g.inflight if g else 0} running ({g.describe() if g else ''}), "
                     f"{fmt_bytes(s.dl_bytes)} at {fmt_bytes(self.speed)}/s")
            if self.dl_paused:
                line += " | PAUSED"
        return line

    def status_panel(self):
        s = self.stats
        el = time.monotonic() - s.started
        q = self.queue.qsize() if self.queue else 0
        avg = s.net_time / s.requests if s.requests else 0.0
        lim = self.lim
        g = Table.grid(padding=(0, 3))
        for _ in range(5):
            g.add_column()
        g.add_row(f"[bold]{s.pages}[/] pages", f"[bold]{q}[/] queued",
                  f"[bold green]{s.dirs}[/] dirs with files", f"[bold]{s.files}[/] files",
                  f"[bold {'red' if s.errors else 'white'}]{s.errors}[/] errors")
        g.add_row(f"{s.pages / max(el, 1e-6):.1f} pages/s", f"avg {avg:.2f}s",
                  f"parallel {lim.limit if lim else 0}/{self.cfg.workers}",
                  f"delay {lim.delay if lim else self.cfg.delay:.2f}s",
                  time.strftime("%H:%M:%S", time.gmtime(el)))
        parts = [g]
        if self.dlq is not None:
            gate = self.gate
            d = Table.grid(padding=(0, 3))
            for _ in range(5):
                d.add_column()
            d.add_row(f"[bold cyan]{s.dl_new}[/] new", f"[bold cyan]{s.dl_updated}[/] updated",
                      f"{s.dl_resumed} resumed", f"{s.dl_uptodate} up to date",
                      f"[bold {'red' if s.dl_failed else 'white'}]{s.dl_failed}[/] failed")
            d.add_row(f"[bold]{self.dlq.qsize()}[/] waiting",
                      f"[bold]{gate.inflight if gate else 0}[/] downloading",
                      f"({gate.describe() if gate else ''})",
                      fmt_bytes(s.dl_bytes), f"[bold]{fmt_bytes(self.speed)}/s[/]")
            parts += ["", d]
            if self.dl_paused:
                parts.append(f"[red]PAUSED: {self.dl_paused}[/]")
            if self.active:
                a = Table.grid(padding=(0, 2))
                a.add_column(no_wrap=True, max_width=60, overflow="ellipsis")
                a.add_column(width=24)
                a.add_column(justify="right", no_wrap=True)
                for rel, (have, total) in list(self.active.items())[:12]:
                    bar = ProgressBar(total=total or 100, completed=have if total else 0,
                                      width=24, pulse=not total)
                    size = f"{fmt_bytes(have)} / {fmt_bytes(total)}" if total else fmt_bytes(have)
                    a.add_row(rel, bar, size)
                if len(self.active) > 12:
                    a.add_row(f"... and {len(self.active) - 12} more", "", "")
                parts.append(a)
        return Panel(Group(*parts), title=f"dir_crawler - {self.label(self.rules.start)}",
                     title_align="left", border_style="cyan")

    # ---- output ----
    def output_paths(self):
        stem = os.path.splitext(self.cfg.output)[0]
        return self.cfg.output, stem + ".txt", stem + "_errors.csv", stem + "_files.csv"

    def write_outputs(self, quiet: bool = False):
        csv_path, txt_path, err_path, files_path = self.output_paths()
        agg: dict = {}
        for url, depth, files, exts in self.state.found():
            k = self.rules.dir_key(url)
            a = agg.setdefault(k, {"depth": depth, "files": 0, "exts": set()})
            a["depth"] = min(a["depth"], depth)
            a["files"] += files
            a["exts"].update(exts.split())
        keys = sorted(agg, key=lambda k: self.label(k).casefold())
        written = []

        def save(path, writer, encoding="utf-8-sig"):                      # BOM: Excel-friendly CSV
            tmp = path + ".tmp"
            try:
                with open(tmp, "w", newline="", encoding=encoding) as f:
                    writer(f)
                os.replace(tmp, path)
                written.append(path)
            except PermissionError:
                log.warning("could not write %s - is it open in another program (Excel)?", path)

        def w_csv(f):
            w = csv.writer(f)
            w.writerow(["directory", "name", "depth", "files", "extensions"])
            for k in keys:
                a = agg[k]
                w.writerow([k, self.label(k), a["depth"], a["files"], " ".join(sorted(a["exts"]))])

        save(csv_path, w_csv)
        save(txt_path, lambda f: f.write("".join(k + "\n" for k in keys)), encoding="utf-8")

        if self.track_files or self.state.count("1", "files"):
            listing = not self.cfg.download_dir

            def status(state, note):
                if listing and state == State.QUEUED:
                    return "listed"
                if state == State.QUEUED:
                    return "queued"
                if state == State.FAILED:
                    return "failed"
                return note or ("downloaded" if state == State.DONE else "skipped")

            def w_files(f):
                w = csv.writer(f)
                w.writerow(["url", "directory", "file", "saved_as", "site_date", "site_size",
                            "size", "status", "error"])
                for url, path, d, ext, size, state, error, note, ldate, lsize in self.state.files():
                    w.writerow([url, d, path.rsplit("/", 1)[-1], "" if listing else path,
                                ldate or "", lsize or "", size if size is not None else "",
                                status(state, note), error or ""])
            save(files_path, w_files)

        failed = self.state.failed()
        if failed:
            def w_err(f):
                w = csv.writer(f)
                w.writerow(["kind", "url", "error"])
                w.writerows(failed)
            save(err_path, w_err)
        elif os.path.exists(err_path):
            try:
                os.remove(err_path)
            except OSError:
                pass
        if not quiet:
            return written, agg
        return None

    def finish(self) -> None:
        try:
            self.state.commit()
        except sqlite3.Error:
            pass
        if self.manifest:
            best = self.gate.remembered_max() if self.gate else None
            if best:
                self.manifest.set_meta(self.gate_key, best)
            self.manifest.close()
        written, agg = self.write_outputs()
        s = self.stats
        el = time.monotonic() - s.started
        remaining = self.state.count("state IN (0,2)")
        failed = self.state.count("state=2")
        f_left = self.state.count("state=0", "files") if self.cfg.download_dir else 0
        self.state.close()

        c = self.console
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style="dim")
        t.add_column()
        t.add_row("Pages crawled", str(s.pages))
        t.add_row("Directories with files", f"[bold green]{len(agg)}[/]")
        t.add_row("File links seen", str(s.files))
        t.add_row("Failed pages", f"[red]{failed}[/]" if failed else "0")
        if s.requests:
            t.add_row("Requests / avg response", f"{s.requests} / {s.net_time / s.requests:.2f}s")
        if self.cfg.list_files and not self.cfg.download_dir:
            t.add_row("Archives listed", str(s.dl_found))
        if self.cfg.download_dir:
            t.add_row("New files", f"[bold cyan]{s.dl_new}[/]")
            t.add_row("Updated (changed on site)", f"[bold cyan]{s.dl_updated}[/]")
            t.add_row("Resumed (were incomplete)", str(s.dl_resumed))
            t.add_row("Already up to date", str(s.dl_uptodate))
            t.add_row("Failed downloads", f"[red]{s.dl_failed}[/]" if s.dl_failed else "0")
            t.add_row("Downloaded", fmt_bytes(s.dl_bytes))
            if self.gate and self.gate.auto:
                best = self.gate.remembered_max()
                t.add_row("Parallel downloads", f"peak {self.gate.peak}"
                          + (f", server allows {best} (remembered for next time)" if best else ""))
            t.add_row("Saved under", os.path.abspath(self.cfg.download_dir))
        t.add_row("Time", time.strftime("%H:%M:%S", time.gmtime(el)))
        c.print()
        c.print(Panel(t, title="Summary", title_align="left", border_style="green"))
        if agg and not self.cfg.download_dir:
            top = sorted(agg.items(), key=lambda kv: -kv[1]["files"])[:10]
            tt = Table(title="Largest directories", title_justify="left", box=None, padding=(0, 2))
            tt.add_column("files", justify="right")
            tt.add_column("directory")
            for k, a in top:
                tt.add_row(str(a["files"]), self.label(k))
            c.print(tt)
        if self.dl_paused:
            c.print(f"[red]Downloads were paused: {self.dl_paused}[/]", soft_wrap=True)
        if remaining or f_left:
            what = []
            if remaining:
                what.append(f"{remaining} pages")
            if f_left:
                what.append(f"{f_left} downloads")
            c.print(f"[yellow]{' and '.join(what)} not finished yet. "
                    f"Run the same command with --resume to continue.[/]", soft_wrap=True)
        for path in written:
            c.print(f"Saved: {path}", soft_wrap=True, highlight=False)
