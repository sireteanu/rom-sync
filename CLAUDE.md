# Rom Sync (lolroms-sync)

Web app (and the original CLI) for keeping local copies of lolroms.com folders in sync.
`lolroms_sync` is the web interface: it scans the whole site into a catalog, shows a folder tree
with tri-state ticks, and syncs the ticked folders. `dir_crawler` is the engine it shares with the
command-line tool (forked from lolroms-downloader v3.2). Python 3.9+, async (httpx), FastAPI.
The user runs it on **Windows** (`start.bat`, F: drive) and will run it in **Docker on Unraid** -
keep both working: Windows paths and console, and PUID/PGID/UMASK plus `/config` and `/data` in Docker.

## Commands
- Install (dev): `pip install -e ".[dev]"`
- Tests: `python -m pytest` (~90 s; all traffic goes to the in-process `tests/mock_site.py`)
- Lint: `python -m pyflakes src tests`
- Web app: `python run.py [--data DIR] [--port 8484] [--host 0.0.0.0] [--password X]`
  (Windows users: `start.bat`; installed: `lolroms-sync`)
- CLI: `python dir-crawler.py URL [--download DIR]` (installed: `dir-crawler`)
- Docker: `docker build -t lolroms-sync .` / `docker compose up -d --build`

## Layout
```
src/lolroms_sync/            the web app
  __main__.py   options / env vars (LOLROMS_DATA, _CONFIG, _HOST, _PORT, _PASSWORD, _DATA_LOCKED)
  web.py        FastAPI routes (/api/status, tree, search, files, select, scan, sync, stop, settings)
  engine.py     one job at a time (scan | sync | scan+sync), progress, LogBuffer, scheduler
  catalog.py    SQLite in <config>/catalog.db: dirs, files, selection, settings, scans, history;
                tree() = folders with recursive totals (cached; invalidate() on structural change)
  scanner.py    reads listing pages (never files) into the catalog; prunes what the site dropped
  static/       index.html, app.css, app.js - no build step, no CDN (works offline on a LAN)
src/dir_crawler/             the engine (also the CLI)
  download.py   Downloader: plan() + download() shared by CLI and web app; run_item() = one worker
                step (pushed-back files go back in the queue and free their slot)
  sync.py       FileInfo, Manifest (<data>/.dir_crawler.db), plan(), site_timestamp(), find_size()
  extract.py    LinkExtractor incl. per-file date/size from the listing row
  limits.py     Limiter (page pacing), DownloadGate (auto parallel downloads), RateLimit
  crawler.py / cli.py / state.py / urls.py / util.py / constants.py / config.py   CLI + helpers
docker/entrypoint.sh, Dockerfile, docker-compose.yml, unraid/lolroms-sync.xml, .github/workflows/
start.bat, run.py, dir-crawler.py   launchers (hyphen/`run` names so they never shadow the packages)
```

## Web app rules
- Folder keys are lolroms `dir` values ("Atari/5200"); the root is "". Ticks are rules on a path
  that apply to everything below, including folders added later; `Catalog.select()` removes rules
  beneath the changed path and drops a rule equal to what it would inherit.
- Endpoints are `async def` on purpose: the catalog's single SQLite connection and the engine
  live on the event loop thread.
- A sync re-reads the ticked folders' listings first (setting `refresh_before_sync`), then plans
  every file with `Downloader.plan()`; unchanged files never hit the network.
- Ticking every subfolder of a folder (that has no files of its own) folds into one tick on that
  folder (`select()` walks up), so its box shows a tick, not "-". Clicking a "-" box ticks all.
- lolroms links some top-level sections (Arcade, Pokéhacks, Audio [FLAC], fixdats) **only from its
  header bar**, which the extractor skips as navigation. `scanner.header_dirs()` adds every
  one-segment `?dir=` link from the root page during a full scan. Mock: `HEADER_DIRS`.
- Column sorting (Folder/Files/Size/Updated/Synced: asc -> desc -> name order) is client-side in
  app.js (`reorder()`); empty folders always sort last. Choice kept in localStorage.
- Status `sync` has `bytes_planned`, `bytes_left`, `files_left`, `eta` (seconds, from a ~2 min speed
  average) for the "This sync" box.
- **Interrupted syncs resume**: `Engine.start()` stores `interrupted_job`; it's cleared when the
  job ends on its own or by Stop, but not by `shutdown()` (app closing), so `scheduler_loop`
  restarts it on the next start (setting `resume_interrupted`). Ticks are committed per click.
- **Restart**: POST /api/restart -> `on_restart` from `__main__` stops uvicorn, then re-runs
  `python -m lolroms_sync` (exec on Linux/Docker keeps the PID; Windows runs it as a child).
- **Startup**: a busy port that answers /api/health means Rom Sync already runs -> open it, exit 0.
  Logs also go to `<config>/rom-sync.log`. `run.py` pauses on errors (double-click on Windows)
  and re-launches itself with `.venv` when the current Python lacks the dependencies.
- **RomM** (`romm.py`, `romm_slugs.py` vendored from rommapp/romm platform_slugs.py): resolves
  each folder to platform/merge/bios/skip/unmapped from `RULES` + user map (settings `romm`),
  groups by slug, mounts the common folder, and writes compose / Unraid / config.yml. RomM facts it
  relies on are in the module docstring - re-check them against RomM's config_manager.py when
  RomM changes. Never move or rename the user's folders for RomM.
- The display name is **Rom Sync**; package, module, image and folder names stay `lolroms-sync`.
- Interface copy: plain sentence-case verbs ("Scan site", "Sync selected"); errors say what to do.
  Dark is the default theme; light via the header toggle (localStorage).

## How it works (keep these invariants)
1. **File links are recognised from the URL alone and never fetched while crawling.**
   lolroms file links look like `/?file=Atari%2F5200%2FGame%20(USA).7z` — the extension is in a
   *query value*, not the path. `UrlRules.file_ext()` and the fast path in `LinkExtractor.extract()`
   check both. Only `--download` ever requests a file URL. (Regression history: v2 treated these
   as pages, fetched every file and collapsed them onto the site root, which caused a crawl loop.)
2. **Scope**: only the start listing and its subfolders are crawled. For `?dir=Atari`, a URL is in
   scope if its `dir` value equals `Atari` or starts with `Atari/` (case-insensitive). The sidebar
   (all top-level dirs), the logo link to `/` and nav links are all out of scope or ignored.
3. **Canonical URLs**: only the start URL's query params are kept (`dir`), so `&sort=` variants
   dedupe to one request. `--keep-params all` disables this.
4. **Ignored page regions**: `<nav>`, site-level `<header>`, role=navigation/banner, NAV_TOKENS
   class/ids (unless `--include-nav`), and always `.sortbar` / `.back` (lolroms' sort bar and
   "⬅ Back" link).
5. **Downloads** go to `<folder>/<path from the file URL>`, written as `.part` and renamed only when
   complete; retries resume with `Range` + `If-Range`. A `text/html` response for a file is rejected,
   and archive headers are checked against `MAGIC`. The file's mtime is set to the site's date.
5a. **Sync** (`sync.plan`): each listed file is compared with the disk and the manifest in the download
   folder (what the listing showed when it was saved + Last-Modified/ETag). Real lolroms markup
   (verified in a browser, Oct 2026): `<div class="row"><a href="?file=…">📄 name</a>
   <span class="file-meta">2025-11-17 • 3.54 KB</span></div>` — no table, date and size share one
   span. `LinkExtractor._row_info` reads that and table rows (`tests/mock_site.py` mode `tables`);
   the link text is never used (names contain dates). lolroms sends **no Last-Modified or ETag**
   for files, so the listing date is the only date: saved files get it at 12:00 local time.
   new / missing -> download; `.part` or smaller than recorded -> resume; listing date or size differs
   from the manifest -> re-download (UPDATE); unchanged -> SKIP with **no request**. Files not in the
   manifest (put there by hand) are ADOPTED if their size fits the listing size, otherwise
   resumed/updated. Sites without dates/sizes use conditional GET (If-None-Match/If-Modified-Since -> 304).
5b. **DownloadGate** (auto parallel downloads): ramps +1 while every slot is busy and files wait; on
   push-back (429/503, refused connection, HTML instead of file) sets ceiling = running-1; re-probes
   ceiling+1 every `--dl-probe-secs`; a level that worked before needs 2 push-backs within 10 s to be
   lowered; the limit is stored per host in the manifest (`max_parallel:<host>`). Push-back retries
   don't use up a file's normal retries. Repeated HTML pages with no successes -> downloads pause
   (daily limit) and stay queued for `--resume`.
6. **State**: every page/file has a row in `<output>.state.db`; nothing is "done" until its row says so,
   so Ctrl+C at any point is safe and `--resume` continues. Without `--resume`, an unfinished state
   is refused (exit 2) unless `--fresh` is passed.
7. **Politeness**: `Limiter` halves parallelism and doubles the delay on 429/503/timeouts/slow
   responses and recovers gradually. Downloads default to 2 at a time. Respect robots.txt by default.

## Publishing (GitHub: sireteanu/rom-sync)
- Claude pushes to `main` from this session; the user's Unraid server builds the image from GitHub
  (`unraid/docker-compose.yml`, Compose Manager, `pull_policy: build`) - after a push the user
  clicks Compose Up. On Windows the user can run `publish.bat` (commit + pull --rebase + push).
- Releases: bump `version` in `pyproject.toml` and `src/lolroms_sync/__init__.py`, push; the
  `Release` workflow creates `v<version>` with generated notes. (This session can't call the
  releases API directly.) `docker.yml` (registry image) runs only when started by hand.
- `git fetch origin main` before pushing from a shallow clone.

## Conventions
- Dependencies: httpx[http2], selectolax, rich, fastapi, uvicorn (+ pytest/pyflakes for dev). Ask before adding more.
- Log through `logging.getLogger("dircrawl")`; the CLI decides rich vs plain output. Never `print`
  inside the crawler (it breaks the live panel).
- Every user-visible behaviour change gets a test in `tests/` against `MockSite`. If a real-site
  quirk is discovered, reproduce it in `tests/mock_site.py` first, then fix.
- **Tests and development never touch the real lolroms.com.** Only run the tool against the real
  site when the user asks, and never start a `--download` of the real site unless they asked for it
  (it can be hundreds of GB).
- Keep the CLI backwards compatible: existing flags keep their meaning; new behaviour behind new flags.
- Output CSVs are written with a UTF-8 BOM (Excel on Windows); `.txt` lists are plain UTF-8.
  Writes go through `.tmp` + `os.replace` and tolerate the file being open in Excel.

## Gotchas
- `asyncio.Condition` must be created inside the running loop (Limiter is built in `Crawler.run`).
- The rich `Live` panel is refreshed from the ticker coroutine (auto_refresh off) to avoid
  cross-thread reads of crawler state.
- `selectolax.parser` (Modest) is deprecated; use `selectolax.lexbor`.
- Listing dates have no time zone, so for adopted files a site date only counts as "newer" when it is
  more than a day after the local mtime. Files the tool downloaded compare exact listing text instead.
- `MockSite` must copy the live site's markup. v3.2.0/3.2.1 parsed a guessed table layout, the mock
  used the same guess, tests passed, and no real lolroms dates were ever read. When unsure, look
  at the real page (browser / get_page_text) before changing the parser or the mock.
- Tests that measure concurrency rely on `MockSite` counting a download finished *before* its last
  bytes go out; otherwise the client can reuse a slot before the server notices and tests flake.
- Windows: `fs_path()` adds the `\\?\` prefix for long paths; `safe_segment()` strips `<>:"|?*`
  and reserved names (CON, NUL, COM1 ...).
