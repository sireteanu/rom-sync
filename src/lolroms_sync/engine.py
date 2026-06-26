"""The engine behind the web interface: one job at a time (scan or sync), progress, log, schedule."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from dir_crawler.config import Config
from dir_crawler.constants import ARCHIVE_EXTENSIONS, DOWNLOAD_EXTENSIONS
from dir_crawler.download import Downloader, run_item
from dir_crawler.limits import DownloadGate, RateLimit
from dir_crawler.sync import FileInfo, Manifest, size_range
from dir_crawler.util import fmt_bytes, parse_size

from . import __version__
from .catalog import Catalog
from .scanner import Scanner

log = logging.getLogger("dircrawl")
USER_AGENT = f"Mozilla/5.0 (compatible; lolroms-sync/{__version__})"


class LogBuffer(logging.Handler):
    """Keeps the last lines of the log for the web interface."""

    def __init__(self, size: int = 400):
        super().__init__(logging.INFO)
        self.lines: deque = deque(maxlen=size)
        self.counter = 0

    def emit(self, record):
        try:
            self.counter += 1
            self.lines.append({"id": self.counter, "t": record.created, "level": record.levelname.lower(),
                               "msg": record.getMessage()})
        except Exception:  # pragma: no cover
            pass

    def since(self, last_id: int) -> list:
        return [l for l in self.lines if l["id"] > last_id]


def parse_types(text) -> Optional[frozenset]:
    t = (text or "archives").strip().lower()
    if t == "archives":
        return ARCHIVE_EXTENSIONS
    if t == "all":
        return None
    return frozenset("." + e.strip().lstrip(".") for e in t.split(",") if e.strip())


class Engine:
    def __init__(self, config_dir: str, data_dir: str, base_url: str = "https://lolroms.com/", *,
                 data_locked: bool = False, scan_workers: int = 8, scan_delay: float = 0.1,
                 ignore_robots: bool = False, tuning: Optional[dict] = None):
        os.makedirs(config_dir, exist_ok=True)
        self.config_dir = config_dir
        self.base = base_url if base_url.endswith("/") else base_url + "/"
        self.catalog = Catalog(os.path.join(config_dir, "catalog.db"))
        self.data_locked = data_locked
        s = self.catalog.settings()
        if data_locked or not s.get("data_dir"):
            self.catalog.save_settings({"data_dir": data_dir})
        self.scan_workers, self.scan_delay = scan_workers, scan_delay
        self.ignore_robots = ignore_robots
        self.tuning = tuning or {}
        self.logbuf = LogBuffer()
        log.addHandler(self.logbuf)
        log.setLevel(logging.DEBUG)
        self.job: Optional[dict] = None
        self.task: Optional[asyncio.Task] = None
        self.scanner: Optional[Scanner] = None
        self.downloader: Optional[Downloader] = None
        self.gate: Optional[DownloadGate] = None
        self.sync_stats: dict = {}
        self.speed = 0.0
        self._samples: deque = deque(maxlen=6)          # ~5 s window: the speed shown
        self._eta_samples: deque = deque(maxlen=121)    # ~2 min window: steadier, for the ETA
        self.eta_speed = 0.0
        self.stop_requested = False
        self.scheduler: Optional[asyncio.Task] = None
        self.next_run: Optional[float] = None
        self.shutting_down = False

    # ------------------------------------------------------------------ settings
    @property
    def data_dir(self) -> str:
        return self.catalog.settings()["data_dir"]

    def update_settings(self, values: dict) -> dict:
        clean = {}
        s = self.catalog.settings()
        for k, v in values.items():
            if k not in s:
                continue
            if k == "data_dir" and self.data_locked:
                continue
            if k in ("parallel_max", "sync_every_hours", "full_scan_every_days"):
                v = max(0, int(float(v or 0)))
            if k in ("interrupted_job", "romm"):
                continue
            if k in ("refresh_before_sync", "check_remote", "resume_interrupted"):
                v = bool(v)
            if k in ("max_rate", "min_free") and v:
                parse_size(str(v))               # validates, raises on nonsense
            if k == "parallel" and str(v).strip().lower() != "auto":
                v = str(max(1, int(v)))
            if k == "types":
                parse_types(v)
            clean[k] = v
        self.catalog.save_settings(clean)
        self._schedule_next()
        return self.catalog.settings()

    # ------------------------------------------------------------------ jobs
    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, kind: str, root: str = "", scheduled: bool = False) -> bool:
        if self.busy:
            return False
        self.stop_requested = False
        self.job = {"kind": kind, "root": root, "phase": "starting", "started": time.time(),
                    "scheduled": scheduled, "message": ""}
        if kind in ("sync", "scan+sync"):
            # remembered until the sync ends on its own or is stopped, so a sync cut off by a
            # crash, reboot or app restart carries on (and resumes its .part files) at next start
            self.catalog.save_settings({"interrupted_job": kind})
        self.task = asyncio.create_task(self._run(kind, root))
        return True

    def shutdown(self) -> None:
        """The app is closing: stop the job but remember an unfinished sync."""
        self.shutting_down = True
        self.stop()

    def stop(self) -> None:
        self.stop_requested = True
        if self.scanner:
            self.scanner.stop()
        if self.downloader:
            self.downloader.stop_requested = True
        log.info("Stopping...")

    def _client(self) -> httpx.AsyncClient:
        try:
            import h2  # noqa: F401
            http2 = True
        except ImportError:
            http2 = False
        n = self.scan_workers + int(self.catalog.settings().get("parallel_max") or 16) + 2
        return httpx.AsyncClient(http2=http2, follow_redirects=True, max_redirects=8,
                                 timeout=httpx.Timeout(15.0, connect=10.0),
                                 limits=httpx.Limits(max_connections=n, max_keepalive_connections=n),
                                 headers={"User-Agent": USER_AGENT})

    async def _robots(self, client) -> Optional[RobotFileParser]:
        if self.ignore_robots:
            return None
        p = urlsplit(self.base)
        try:
            r = await client.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=10)
            if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
                return rp
        except httpx.HTTPError:
            pass
        return None

    async def _run(self, kind: str, root: str) -> None:
        started = time.time()
        summary: dict = {}
        try:
            async with self._client() as client:
                robots = await self._robots(client)
                if kind in ("scan", "scan+sync"):
                    self.job["phase"] = "reading the site"
                    summary["scan"] = await self._scan(client, robots, root)
                if kind in ("sync", "scan+sync") and not self.stop_requested:
                    summary["sync"] = await self._sync(client, robots)
            self.job["phase"] = "stopped" if self.stop_requested else "done"
        except Exception as e:
            log.exception("job failed: %r", e)
            self.job["phase"] = "failed"
            self.job["message"] = str(e)
            summary["error"] = repr(e)
        finally:
            self.job["finished"] = time.time()
            if kind in ("sync", "scan+sync") and not self.shutting_down:
                self.catalog.save_settings({"interrupted_job": ""})
            self.catalog.add_history(kind, started, summary)
            self.scanner = None
            self.catalog.commit()
            self.catalog.invalidate()            # final numbers show right away
            self._schedule_next()

    async def _scan(self, client, robots, root: str = "", roots=None) -> dict:
        out = {}
        for r in (roots if roots is not None else [root]):
            if self.stop_requested:
                break
            self.scanner = Scanner(self.catalog, self.base, client, workers=self.scan_workers,
                                   delay=self.scan_delay, user_agent=USER_AGENT, robots=robots)
            out[r or "/"] = await self.scanner.run(r)
        return out

    async def _sync(self, client, robots) -> dict:
        s = self.catalog.settings()
        types = parse_types(s["types"])
        data_dir = s["data_dir"]
        os.makedirs(data_dir, exist_ok=True)
        roots = self.catalog.selection_roots()
        if not roots:
            log.warning("Nothing is selected - tick the folders you want, then sync.")
            return {"files": 0}
        if s["refresh_before_sync"]:
            self.job["phase"] = "refreshing the selected folders"
            await self._scan(client, robots, roots=roots)
            if self.stop_requested:
                return {}
        self.scanner = None
        self.job["phase"] = "checking what needs downloading"
        files = self.catalog.files_under(self.catalog.selected_dirs(), types)
        recognised = DOWNLOAD_EXTENSIONS | (types or frozenset())
        cfg = Config(start_url=self.base, download_dir=data_dir, check_remote=bool(s["check_remote"]),
                     min_free=parse_size(str(s["min_free"] or "0")), download_exts=recognised,
                     user_agent=USER_AGENT)
        manifest = Manifest(data_dir)
        host = urlsplit(self.base).hostname or ""
        key = f"max_parallel:{host}"
        known = manifest.get_meta(key)
        auto = str(s["parallel"]).strip().lower() == "auto"
        t = self.tuning
        self.gate = DownloadGate(auto=auto, start=2 if auto else int(s["parallel"]),
                                 hard_max=int(s["parallel_max"] or 16) if auto else int(s["parallel"]),
                                 known_max=int(known) if known and known.isdigit() else None,
                                 ramp_secs=t.get("ramp", 3.0), probe_secs=t.get("probe", 120.0),
                                 confirm_secs=t.get("confirm", 30.0))
        rate = parse_size(str(s["max_rate"])) if s["max_rate"] else 0
        st = self.sync_stats = {"total": len(files), "planned": 0, "to_fetch": 0, "new": 0, "updated": 0,
                                "resumed": 0, "up to date": 0, "already in folder": 0, "failed": 0,
                                "bytes_planned": 0, "bytes_settled": 0, "files_left": 0}

        def settle(url):
            if url in sizes:
                st["bytes_settled"] += sizes.pop(url)
                st["files_left"] -= 1

        def on_done(url, rel, word, size):
            st[word] = st.get(word, 0) + 1
            settle(url)
            self.catalog.set_local(url, word, size, None)

        def on_failed(url, rel, err):
            st["failed"] += 1
            settle(url)
            self.catalog.set_local(url, None, None, err)

        allowed = (lambda u: robots.can_fetch(USER_AGENT, u)) if robots else None
        self.downloader = dl = Downloader(cfg, client, manifest, self.gate, root=data_dir,
                                          bucket=RateLimit(rate) if rate else None, allowed=allowed,
                                          on_done=on_done, on_failed=on_failed)
        queue: asyncio.Queue = asyncio.Queue()
        claimed = [0]                            # taken from the queue, not finished yet
        dl.pending = lambda: queue.qsize() + claimed[0]
        sizes = {}
        for url, rel, d, ext, date, size in files:
            if self.stop_requested:
                break
            info = FileInfo(ext, date, size)
            action, reason = dl.plan(url, rel, info)
            st["planned"] += 1
            if action:
                queue.put_nowait((url, rel, info, action, reason))
                st["to_fetch"] += 1
                st["files_left"] += 1
                rng = size_range(size) if size else None
                sizes[url] = (rng[0] + rng[1]) // 2 if rng else 0
                st["bytes_planned"] += sizes[url]
            if st["planned"] % 2000 == 0:
                await asyncio.sleep(0)               # keep the web interface responsive
        self.catalog.commit()
        log.info("Sync: %d files in the selection, %d to download (about %s), %d already up to date",
                 st["total"], st["to_fetch"], fmt_bytes(st["bytes_planned"]),
                 st["up to date"] + st["already in folder"])
        self.job["phase"] = "downloading"
        self._samples.clear()
        self._eta_samples.clear()
        self.eta_speed = 0.0

        async def worker():
            while True:
                item = await queue.get()
                claimed[0] += 1
                try:
                    await run_item(dl, self.gate, queue, item)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    on_failed(item[0], item[1], f"internal error: {e!r}")
                finally:
                    claimed[0] -= 1
                    queue.task_done()

        async def ticker():
            while True:
                await asyncio.sleep(0.25)
                if not dl.paused:
                    await self.gate.tick(queue.qsize())
                now = time.monotonic()
                if not self._samples or now - self._samples[-1][0] >= 1:
                    self._samples.append((now, dl.bytes))
                    t0, b0 = self._samples[0]
                    self.speed = (dl.bytes - b0) / (now - t0) if now > t0 else 0.0
                    self._eta_samples.append((now, dl.bytes))
                    t1, b1 = self._eta_samples[0]
                    self.eta_speed = (dl.bytes - b1) / (now - t1) if now - t1 >= 3 else 0.0
                    manifest.commit()
                    self.catalog.commit()

        n = self.gate.hard_max if auto else self.gate.limit
        tasks = [asyncio.create_task(worker()) for _ in range(n)] + [asyncio.create_task(ticker())]
        try:
            await queue.join()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            best = self.gate.remembered_max()
            if best:
                manifest.set_meta(key, best)
            manifest.close()
            self.catalog.commit()
            self.speed = self.eta_speed = 0.0
        summary = {k: st[k] for k in ("total", "to_fetch", "new", "updated", "resumed", "up to date",
                                      "already in folder", "failed")}
        summary["bytes"] = dl.bytes
        summary["paused"] = dl.paused
        summary["server_max"] = best
        log.info("Sync %s: %d new, %d updated, %d resumed, %d failed, %s downloaded%s",
                 "stopped" if self.stop_requested else ("paused" if dl.paused else "finished"),
                 st["new"], st["updated"], st["resumed"], st["failed"], fmt_bytes(dl.bytes),
                 f" - paused: {dl.paused}" if dl.paused else "")
        return summary

    # ------------------------------------------------------------------ schedule
    def _schedule_next(self) -> None:
        s = self.catalog.settings()
        hours = float(s.get("sync_every_hours") or 0)
        if hours <= 0:
            self.next_run = None
            return
        last = next((h for h in self.catalog.history(50) if h["kind"] in ("sync", "scan+sync")), None)
        base = last["finished"] if last and last.get("finished") else time.time()
        self.next_run = max(base + hours * 3600, time.time() + 60)

    def resume_interrupted(self) -> bool:
        s = self.catalog.settings()
        if not s.get("interrupted_job") or self.busy:
            return False
        if not s.get("resume_interrupted", True):
            self.catalog.save_settings({"interrupted_job": ""})
            return False
        log.info("The last sync didn't finish (the app was closed or stopped) - continuing it")
        return self.start("sync")

    async def scheduler_loop(self, resume_delay: float = 3.0) -> None:
        self._schedule_next()
        await asyncio.sleep(resume_delay)
        self.resume_interrupted()
        while True:
            await asyncio.sleep(30)
            if self.next_run and time.time() >= self.next_run and not self.busy:
                s = self.catalog.settings()
                days = float(s.get("full_scan_every_days") or 0)
                full = self.catalog.last_scan("full")
                need_full = days > 0 and (not full or time.time() - (full["finished"] or 0) > days * 86400)
                log.info("Scheduled run: %s", "full site scan + sync" if need_full else "sync")
                self.start("scan+sync" if need_full else "sync", scheduled=True)

    # ------------------------------------------------------------------ status for the UI
    def status(self, log_since: int = 0) -> dict:
        s = self.catalog.settings()
        types = parse_types(s["types"])
        tree = self.catalog.tree(types)
        rules = self.catalog.rules()
        sel = {"dirs": 0, "files": 0, "bytes": 0, "synced": 0, "synced_bytes": 0}
        for path, node in tree.items():
            if self.catalog.effective(path, rules):
                sel["dirs"] += 1
                sel["files"] += node.own_files
                sel["bytes"] += node.own_bytes
                sel["synced"] += node.own_synced
                sel["synced_bytes"] += node.own_synced_bytes
        root = tree.get("")
        full = self.catalog.last_scan("full")
        out = {
            "version": __version__,
            "busy": self.busy,
            "job": dict(self.job) if self.job else None,
            "catalog": {"dirs": len(tree) - 1, "files": root.files if root else 0,
                        "bytes": root.bytes if root else 0, "last_full_scan": full},
            "selection": sel,
            "next_run": self.next_run,
            "data_dir": s["data_dir"],
            "log": self.logbuf.since(log_since),
        }
        if self.scanner is not None:
            out["scan"] = self.scanner.progress()
        if self.downloader is not None and self.job and self.job.get("phase") in ("downloading", "done", "stopped"):
            dl, g = self.downloader, self.gate
            st = self.sync_stats
            active = list(dl.active.items())
            # what this sync still has to transfer: planned sizes of unfinished files minus what the
            # files in progress already have on disk (listing sizes are rounded, so it's an estimate)
            left = max(0, st["bytes_planned"] - st["bytes_settled"] - sum(h for _, (h, _t) in active))
            running = self.busy and self.job.get("phase") == "downloading"
            eta = left / self.eta_speed if running and self.eta_speed > 0 and not dl.paused else None
            out["sync"] = dict(st, bytes=dl.bytes, speed=self.speed, paused=dl.paused,
                               bytes_left=left, eta=round(eta) if eta is not None else None,
                               running=g.inflight if g else 0, limit=g.limit if g else 0,
                               gate=g.describe() if g else "",
                               active=[{"rel": r, "have": h, "total": t} for r, (h, t) in active[:20]])
        return out
