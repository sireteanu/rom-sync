"""The engine behind the web interface: one job at a time (scan or sync), progress, log, schedule."""
from __future__ import annotations

import asyncio
import json
import importlib.util
import logging
import os
import time
from collections import deque
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .core.config import Config
from .core.constants import ARCHIVE_EXTENSIONS, DOWNLOAD_EXTENSIONS
from .core.download import Downloader, run_item
from .core.limits import DownloadGate, RateLimit
from .core.sync import MANIFEST_NAME, FileInfo, Manifest, size_range, stamp_folders
from .core.util import fmt_bytes, parse_size, safe_segment

from . import __version__
from .catalog import Catalog, SET_DATE, parent_of, renamed_path
from .core.urls import UrlRules
from .folder_map import FolderMap, clean_place, from_old_settings
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



def parse_mode(value) -> str:
    """'777', '0777', '00777' or '' -> normalised '777' / '' (keep the system default)."""
    v = str(value or "").strip()
    if not v:
        return ""
    digits = v.lstrip("0") or "0"
    if not (len(digits) <= 3 and all(c in "01234567" for c in digits)):
        raise ValueError(f"file permissions must be octal like 777 or 775, not {v!r}")
    return digits.rjust(3, "0")

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
        self._keep_romm_settings()            # after the log is set up, so it shows in the page
        self.fmap = self._load_places()
        self._remap(at_start=True)
        self._log_startup()
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
        self.leftovers: Optional[dict] = None   # Clean up summary from the last check

    # ------------------------------------------------------------------ where folders are saved
    def _load_places(self) -> FolderMap:
        """The "Saved as" settings; Folder map rules and names from 1.3.10-1.4.2 are converted
        once (the rules dialog is gone, everything lives in the tree now)."""
        s = self.catalog.settings()
        places, kids = dict(s.get("folder_places") or {}), dict(s.get("folder_kids") or {})
        old_rules, old_names = s.get("folder_map") or [], s.get("folder_names") or {}
        if old_rules or old_names:
            folders = list(self.catalog.tree(None))
            p2, _ = from_old_settings(old_rules, old_names, folders)
            for k, v in p2.items():
                places.setdefault(k, v)
            self.catalog.save_settings({"folder_places": places, "folder_map": [], "folder_names": {}})
            log.info("Saved as: %d folder settings taken over from the Folder map", len(p2))
        return FolderMap(places, kids)

    def rel_of(self, url: str) -> str:
        """Where a file URL is saved, relative to the download folder ("Saved as" applied)."""
        if getattr(self, "_url_rules", None) is None:
            self._url_rules = UrlRules(Config(start_url=self.base, keep_params=frozenset({"dir"})))
        return self._url_rules.rel_path(url, self.fmap)

    def _remap(self, at_start: bool = False) -> int:
        n = self.catalog.remap(self.rel_of)
        if n:
            log.info("Saved as: %d files now have a different place in the download folder%s", n,
                     "" if at_start else " - the next sync puts them there")
        return n

    def set_place(self, path: str, place, kids: bool = False) -> dict:
        """The tree's "Saved as" column: where this site folder goes (kids=False), or where its
        subfolders go (kids=True). "" = the default. ValueError says what to fix."""
        if self.busy:
            raise RuntimeError("busy")
        path = (path or "").strip("/")
        if not path and not kids:
            raise ValueError("pick a folder")
        value = clean_place(place, kids=kids)
        if value.startswith("/") and not kids:
            # the column shows and takes full paths: store them relative to where the folder sits
            # anyway, so a folder kept below its parent moves along when the parent moves
            others = FolderMap({k: v for k, v in self.fmap.places.items() if k.casefold() != path.casefold()},
                               self.fmap.kids)
            value = others.as_typed(path, value)
        key = "folder_kids" if kids else "folder_places"
        cur = dict(self.fmap.kids if kids else self.fmap.places)
        cur = {k: v for k, v in cur.items() if k.casefold() != path.casefold()}
        if value and not (not kids and value == safe_segment(path.rsplit("/", 1)[-1])):
            cur[path] = value
        self.catalog.save_settings({key: cur})
        self.fmap = FolderMap(cur, self.fmap.kids) if not kids else FolderMap(self.fmap.places, cur)
        what = f"subfolders of {path or 'the site'}" if kids else path
        if path in cur:
            log.info("Saved as: %s go%s to %s", what, "" if kids else "es",
                     "the download folder" if cur[path] == "/" else cur[path])
        else:
            log.info("Saved as: %s back to %s", what, "their own places" if kids else "its own name")
        moved = self._remap()
        return {"path": path, "place": cur.get(path, ""), "moved": moved}

    def _log_startup(self) -> None:
        """Say which catalog and download folder this start uses and what's in them, so a wrong
        folder mapping (an empty catalog after an update) is easy to spot in the log."""
        cat = self.catalog
        try:
            st = os.stat(cat.path)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
            size = fmt_bytes(st.st_size)
        except OSError:
            when, size = "?", "?"
        c = cat.counts()
        ticks = sum(1 for v in cat.rules().values() if v)
        full = cat.last_scan("full")
        if full:
            scanned = "last full scan %s (%s)" % (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(full.get("started") or 0)), full.get("status"))
        else:
            scanned = "no full scan yet"
        if cat.existed:
            log.info("Catalog: %s (%s, last changed %s)", cat.path, size, when)
        else:
            log.warning("Catalog: %s did not exist - started a new, empty catalog", cat.path)
        log.info("Catalog has %d folders, %d files (%s), %d ticked folders; %s",
                 c["dirs"], c["files"], fmt_bytes(c["bytes"]), ticks, scanned)

        data = self.data_dir
        manifest = os.path.join(data, MANIFEST_NAME)
        try:
            with os.scandir(data) as it:
                entries = [e.name for e in it if not e.name.startswith(".")]
            log.info("Download folder: %s (%d items at the top%s; download record %s)", data, len(entries),
                     ": " + ", ".join(sorted(entries)[:8]) + (" ..." if len(entries) > 8 else "") if entries else "",
                     "found" if os.path.exists(manifest) else "not found")
        except FileNotFoundError:
            entries = []
            log.warning("Download folder: %s does not exist yet", data)
        except OSError as e:
            entries = []
            log.warning("Download folder: %s can't be read (%s)", data, e)
        if not c["dirs"] and (entries or os.path.exists(manifest)):
            log.warning("The catalog is empty but the download folder already has files. If Rom Sync "
                        "ran before, check that the config folder mapping (%s) points at the folder that "
                        "holds your old catalog.db - your ticks are stored there.", self.config_dir)

    def _keep_romm_settings(self) -> None:
        """The RomM Mapper moved to ROM Mapper (1.3.5). Choices made here are left in
        <config>/romm-mapper.json - written once from the catalog if that file is missing - so
        they can be copied over. Nothing else reads them any more."""
        cur = self.catalog.settings().get("romm") or {}
        path = os.path.join(self.config_dir, "romm-mapper.json")
        if not cur or os.path.exists(path):
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, path)
            log.info("Your RomM Mapper choices are in %s - copy that file to ROM Mapper to keep them", path)
        except OSError:
            pass

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
            if k == "outage_wait_hours":
                v = max(0.0, float(v or 0))
                v = int(v) if v == int(v) else v
            if k in ("interrupted_job", "folder_map", "folder_names", "folder_places", "folder_kids"):
                continue
            if k in ("refresh_before_sync", "check_remote", "resume_interrupted"):
                v = bool(v)
            if k == "file_mode":
                v = parse_mode(v)
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
        what = {"scan": "site scan" if not root else f"scan of {root}", "sync": "sync",
                "scan+sync": "site scan + sync"}.get(kind, kind)
        log.info("Starting %s (%s)", what, "scheduled" if scheduled else "requested")
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
        http2 = importlib.util.find_spec("h2") is not None
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
            self.catalog.prune_changes()
            self.scanner = None
            self.catalog.commit()
            self.catalog.invalidate()            # final numbers show right away
            self._schedule_next()

    async def _scan(self, client, robots, root: str = "", roots=None) -> dict:
        out: dict = {}
        renamed: dict = {}

        async def run(r: str, recurse: bool = True) -> dict:
            self.scanner = Scanner(self.catalog, self.base, client, workers=self.scan_workers,
                                   delay=self.scan_delay, user_agent=USER_AGENT, robots=robots,
                                   folder_map=self.fmap)
            res = await self.scanner.run(r, recurse)
            renamed.update(res.get("renamed") or {})
            return res

        for r in (roots if roots is not None else [root]):
            if self.stop_requested:
                break
            r = renamed_path(r, renamed)            # an earlier folder in this loop found it renamed
            out[r or "/"] = await run(r)
            if r and r not in self.scanner.read and not self.stop_requested:
                # couldn't read a ticked folder: the site may have renamed it to a newer set date,
                # so read its parent's listing (just that page) to find it and move the ticks
                last = r.rsplit("/", 1)[-1]
                if SET_DATE.search(last):
                    log.info("%s couldn't be read - checking whether the site renamed it", r)
                    await run(parent_of(r), recurse=False)
                    new = renamed_path(r, renamed)
                    if new != r and not self.stop_requested:
                        out[new] = await run(new)
        if renamed:
            self._follow_map_renames(renamed)
        return out

    def _follow_map_renames(self, renamed: dict) -> None:
        """"Saved as" settings of folders the site renamed to a newer set date follow them."""
        rename = lambda p: renamed_path(p, renamed)                     # noqa: E731
        places, kids = FolderMap.renamed(self.fmap.places, rename), FolderMap.renamed(self.fmap.kids, rename)
        if places == self.fmap.places and kids == self.fmap.kids:
            return
        self.catalog.save_settings({"folder_places": places, "folder_kids": kids})
        self.fmap = FolderMap(places, kids)
        log.info("Saved as: renamed folders keep where you save them")
        self._remap()

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
                     file_mode=int(parse_mode(s.get("file_mode", "")), 8) if parse_mode(s.get("file_mode", "")) else None,
                     user_agent=USER_AGENT, retry_wait=self.tuning.get("retry", 2.0),
                     outage_wait=float(s.get("outage_wait_hours", 12) or 0) * 3600,
                     outage_check=self.tuning.get("outage_check", 30.0))
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

        job_id = self.job["started"]

        def on_done(url, rel, word, size):
            st[word] = st.get(word, 0) + 1
            settle(url)
            self.catalog.set_local(url, word, size, None)
            if word in ("new", "updated", "resumed"):
                self.catalog.add_change(job_id, word, rel, size)

        def on_failed(url, rel, err):
            st["failed"] += 1
            settle(url)
            self.catalog.set_local(url, None, None, err)
            self.catalog.add_change(job_id, "failed", rel, None, err)

        allowed = (lambda u: robots.can_fetch(USER_AGENT, u)) if robots else None
        self.downloader = dl = Downloader(cfg, client, manifest, self.gate, root=data_dir,
                                          bucket=RateLimit(rate) if rate else None, allowed=allowed,
                                          on_done=on_done, on_failed=on_failed)
        queue: asyncio.Queue = asyncio.Queue()
        claimed = [0]                            # taken from the queue, not finished yet
        dl.pending = lambda: queue.qsize() + claimed[0]
        sizes = {}

        def plan_all() -> list:
            """Compare every selected file with the disk. Runs in a worker thread: on a network
            share or an array this is thousands of disk lookups, and the web page must stay
            responsive meanwhile. Nothing downloads yet, so nothing else uses the manifest."""
            todo = []
            for url, rel, d, ext, date, size in files:
                if self.stop_requested:
                    break
                info = FileInfo(ext, date, size)
                action, reason = dl.plan(url, rel, info)
                st["planned"] += 1
                if action:
                    todo.append((url, rel, info, action, reason))
                    st["to_fetch"] += 1
                    st["files_left"] += 1
                    rng = size_range(size) if size else None
                    sizes[url] = (rng[0] + rng[1]) // 2 if rng else 0
                    st["bytes_planned"] += sizes[url]
            return todo

        for item in await asyncio.to_thread(plan_all):
            queue.put_nowait(item)
        manifest.commit()
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
                if not dl.paused and not dl.outage:
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
            if dl.touched_dirs:
                n = await asyncio.to_thread(stamp_folders, list(dl.touched_dirs), data_dir)
                if n:
                    log.info("Dated %d folders to their newest file", n)
            if not self.stop_requested and not dl.paused:
                await self._check_leftovers()
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

    # ------------------------------------------------------------------ clean up
    async def find_leftovers(self) -> list:
        """Files in the download folder that aren't part of the sync (see cleanup.py)."""
        from . import cleanup
        s = self.catalog.settings()
        self.catalog.commit()
        types = parse_types(s["types"])
        # walking the folder and judging tens of thousands of files takes a moment: do it in a
        # worker thread so the web page stays responsive (the catalog is safe to read from there)
        files = await asyncio.to_thread(lambda: cleanup.find(self.catalog, types, cleanup.walk(s["data_dir"]), self.fmap))
        self.leftovers = dict(cleanup.summary(files), checked=time.time())
        return files

    async def _check_leftovers(self) -> None:
        try:
            files = await self.find_leftovers()
        except Exception as e:  # never let the check fail a sync
            log.warning("Could not check the download folder for leftover files: %s", e)
            return
        if files:
            log.info("%d files (%s) in the download folder aren't part of the sync any more - "
                     "review them with Clean up", len(files), fmt_bytes(self.leftovers["bytes"]))

    async def delete_leftovers(self, paths) -> dict:
        """Delete the given files, but only those that are still leftovers right now."""
        from . import cleanup
        wanted = set(paths)
        files = [f for f in await self.find_leftovers() if f["path"] in wanted]
        res = await asyncio.to_thread(cleanup.delete, self.data_dir, files)
        res["skipped"] = len(wanted) - len(files)
        gone = set(res["deleted"])
        for f in files:
            if f["path"] in gone:
                self.catalog.add_change(None, "deleted", f["path"], f["size"], cleanup.REASONS[f["reason"]])
        self.catalog.commit()
        self.catalog.forget_local([f["url"] for f in files if f.get("url") and f["path"] in gone])
        log.info(cleanup.describe(res))
        await self.find_leftovers()
        return res

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
    def _selection_totals(self, tree: dict, rules: dict) -> dict:
        """Size of the ticked part of the catalog; worked out again only when the tree (rebuilt
        after scans and downloads) or the ticks change, not on every status poll."""
        key = (id(tree), tuple(sorted(rules.items())))
        cached = getattr(self, "_sel_cache", None)
        if cached and cached[0] == key and cached[2] is tree:
            return dict(cached[1])
        sel = {"dirs": 0, "files": 0, "bytes": 0, "synced": 0, "synced_bytes": 0}
        for path, node in tree.items():
            if self.catalog.effective_files(path, rules):
                sel["dirs"] += 1
                sel["files"] += node.own_files
                sel["bytes"] += node.own_bytes
                sel["synced"] += node.own_synced
                sel["synced_bytes"] += node.own_synced_bytes
        self._sel_cache = (key, dict(sel), tree)
        return sel

    def status(self, log_since: int = 0) -> dict:
        s = self.catalog.settings()
        types = parse_types(s["types"])
        tree = self.catalog.tree(types)
        rules = self.catalog.rules()
        sel = self._selection_totals(tree, rules)
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
            "leftovers": self.leftovers,
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
            eta = (left / self.eta_speed if running and self.eta_speed > 0 and not dl.paused and not dl.outage
                   else None)
            out["sync"] = dict(st, bytes=dl.bytes, speed=self.speed, paused=dl.paused,
                               outage=dict(dl.outage, gives_up=dl.outage["since"] + dl.cfg.outage_wait)
                               if dl.outage and running else None,
                               bytes_left=left, eta=round(eta) if eta is not None else None,
                               running=g.inflight if g else 0, limit=g.limit if g else 0,
                               gate=g.describe() if g else "",
                               active=[{"rel": r, "have": h, "total": t} for r, (h, t) in active[:100]])
        return out
