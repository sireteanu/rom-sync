# Rom Sync (lolroms-sync)

Web app for keeping local copies of lolroms.com folders in sync. `lolroms_sync` scans the whole
site into a catalog, shows a folder tree with tri-state ticks, syncs the ticked folders, cleans up
files that aren't part of the sync (the RomM Mapper moved to sireteanu/rom-mapper in 1.3.5). `lolroms_sync.core` is the sync
engine (forked from lolroms-downloader v3.2; its command-line tool was removed in 1.3.0).
Python 3.9+, async (httpx), FastAPI.
It runs on **Windows** (`start.bat`) and in **Docker on Unraid** -
keep both working: Windows paths and console, and PUID/PGID/UMASK plus `/config` and `/data` in Docker.

## Commands
- Install (dev): `pip install -e ".[dev]"`
- Tests: `python -m pytest` (~90 s; all traffic goes to the in-process `tests/mock_site.py`);
  the `Tests` workflow runs them and pyflakes on every push (Python 3.9 and 3.12)
- Lint: `python -m pyflakes src tests`
- Web app: `python run.py [--data DIR] [--port 8484] [--host 0.0.0.0] [--password X]`
  (Windows users: `start.bat`; installed: `lolroms-sync`)
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
  cleanup.py    Clean up: walk the download folder, judge files (gone / part / unticked), delete
  static/       index.html, app.css, app.js - no build step, no CDN (works offline on a LAN)
  core/         the sync engine
    download.py Downloader: plan() + download(); run_item() = one worker step (pushed-back files
                go back in the queue and free their slot)
    sync.py     FileInfo, Manifest (<data>/.dir_crawler.db), plan(), site_timestamp(), find_size()
    extract.py  LinkExtractor incl. per-file date/size from the listing row
    limits.py   Limiter (page pacing), DownloadGate (auto parallel downloads), RateLimit
    urls.py / util.py / constants.py / config.py   helpers
tests/  conftest (app_factory + API helpers), test_app, test_engine, test_cleanup, test_changes, test_outage, test_units
docker/entrypoint.sh, Dockerfile (deps in their own layer), docker-compose.yml, unraid/, .github/workflows/
start.bat, run.py   launchers (`run` name so it never shadows the package)
```

## Web app rules
- Folder keys are lolroms `dir` values ("Atari/5200"); the root is "". Ticks are rules on a path
  that apply to everything below, including folders added later; `Catalog.select()` removes rules
  beneath the changed path and drops a rule equal to what it would inherit.
- Endpoints are `async def` on purpose: the catalog's single SQLite connection and the engine
  live on the event loop thread.
- A sync re-reads the ticked folders' listings first (setting `refresh_before_sync`), then plans
  every file with `Downloader.plan()`; unchanged files never hit the network.
- Selection key `"<folder>/."` (`catalog.OWN`) = only the files directly in that folder ("Files in
  this folder" row in the tree); `effective_files()` decides which folders' files are synced.
  Ticking every part of a folder (subfolders, and its own files if any) folds into one tick on the
  folder (`_fold()`), so its box shows a tick, not "-". Clicking a "-" box ticks all.
- lolroms links some top-level sections (Arcade, Pokéhacks, Audio [FLAC], fixdats) **only from its
  header bar**, which the extractor skips as navigation. `scanner.header_dirs()` adds every
  one-segment `?dir=` link from the root page during a full scan. Mock: `HEADER_DIRS`.
- Column sorting (Folder/Files/Size/Updated/Synced: asc -> desc -> name order) is client-side in
  app.js (`reorder()`); empty folders always sort last. Choice kept in localStorage.
- **Clean up** (`cleanup.py`): after a sync that wasn't stopped or paused, `Engine._check_leftovers()`
  stores a summary in status `leftovers`. Leftovers: `gone` (not listed by its folder, which was read
  without errors; unknown folders only after a finished full scan), `part` (.part of an unwanted
  file), `unticked`. Never listed: unread/failed folders, dot-files (manifest), symlinks.
  **Nothing is deleted without the user's confirmation**: POST /api/cleanup/delete needs
  `confirm: true`, refuses while busy, and re-checks so only current leftovers are removed; then
  empty folders go, `Manifest.forget()` and `Catalog.forget_local()`. Never delete anything else
  from the download folder.
- **Saved as** column (tree, `folder_names` setting, `/api/folder-name`) is the main way to rename
  folders on disk - the owner renames almost every folder, so keep it fast (Enter / blur saves,
  "−date" button). **Folder map** (`folder_map.py`, setting `folder_map`, `/api/folder-map`,
  ☰ → Folder map) holds `*` rules and moves. Both are edited in the page only (no text file). Saving them, and every
  start, runs `Catalog.remap(engine.rel_of)` so catalog paths always match the rules.
- **Renamed folders**: lolroms renames dated folders when a newer set arrives. `Catalog.follow_renames()`
  (after every finished scan) moves ticks from a vanished folder to its same-stem sibling
  (`catalog.SET_DATE`/`set_stem`); `Engine._scan` re-reads only the parent of a ticked dated folder
  it couldn't read (`recurse=False`), then refreshes the new folder; exact Folder map rules follow.
  Never rename folders on disk automatically.
- **Change history** (`changes` table, `Catalog.add_change`): a sync records new/updated/resumed/failed
  per file (job = the sync's start time), Clean up records deleted; pruned after 365 days. Activity
  counters open `/api/sync/files?kind=`, the header's Activity log uses `/api/changes/days` + `?day=`.
- Header: Scan / Sync / Stop / Activity log, then one menu (☰, `#btn-menu`) with
  Settings, theme, Restart, Shut down.
- Slow disk work stays off the event loop: sync planning (`plan_all()` via `asyncio.to_thread`;
  Manifest uses `check_same_thread=False`, safe because downloads start only after planning) and the
  Clean up check. Planning stats each file once and passes `st` on; never chmod/utime a file whose
  mode/date already match (shares like Unraid's array make each call slow).
- Status `selection` is cached per (tree object, rules) in `Engine._selection_totals()`.
- The page stops polling while its tab is hidden (`visibilitychange`); replies over 1 KB are gzipped
  (`GZipMiddleware`).
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
- **RomM**: the RomM Mapper moved to sireteanu/rom-mapper (1.3.5). `Engine._keep_romm_settings()`
  writes the catalog's old `romm` setting to `<config>/romm-mapper.json` once (if missing) so users
  can copy it; nothing else reads it. Don't add RomM features here.
- The display name is **Rom Sync**; package, module, image and folder names stay `lolroms-sync`.
- Interface copy: plain sentence-case verbs ("Scan site", "Sync selected"); errors say what to do.
  Dark is the default theme; light via the header toggle (localStorage).

## How it works (keep these invariants)
1. **File links are recognised from the URL alone and never fetched while scanning.**
   lolroms file links look like `/?file=Atari%2F5200%2FGame%20(USA).7z` — the extension is in a
   *query value*, not the path. `UrlRules.file_ext()` and the fast path in `LinkExtractor.extract()`
   check both. Only a sync ever requests a file URL. (Regression history: v2 treated these
   as pages, fetched every file and collapsed them onto the site root, which caused a crawl loop.)
2. **Scope**: only the start listing and its subfolders are read. For `?dir=Atari`, a URL is in
   scope if its `dir` value equals `Atari` or starts with `Atari/` (case-insensitive). The sidebar
   (all top-level dirs), the logo link to `/` and nav links are all out of scope or ignored.
3. **Canonical URLs**: only the start URL's query params are kept (`dir`), so `&sort=` variants
   dedupe to one request (`Config.keep_params`).
4. **Ignored page regions**: `<nav>`, site-level `<header>`, role=navigation/banner, NAV_TOKENS
   class/ids (`Config.ignore_nav`), and always `.sortbar` / `.back` (lolroms' sort bar and
   "⬅ Back" link).
5. **Downloads** go to `<folder>/<path from the file URL>` (folder segments go through the user's
   Folder map, `folder_map.py`, in `UrlRules.rel_path()`; file names never change; Clean up maps
   catalog folders to disk with the same `FolderMap.disk_folder()`, so both must stay in step;
   no automatic renaming of folders - the owner wants mapping done only by their rules), written as `.part` and renamed only when
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
   ceiling+1 every 2 min (`tuning["probe"]`); a level that worked before needs 2 push-backs within 10 s to be
   lowered; the limit is stored per host in the manifest (`max_parallel:<host>`). Push-back retries
   don't use up a file's normal retries. Repeated HTML pages with no successes -> downloads pause
   (daily limit) and stay queued for the next sync.
5c. **Site outages**: site-caused errors (5xx/52x, timeouts, connect/protocol errors) count per file;
   after 3, `site_up()` checks the start URL. Down -> `Downloader.outage`, the file is re-queued without
   using its retries and every worker waits in `wait_for_site()` (one probes; 30 s doubling to 10 min;
   gate ramp frozen); gives up after `outage_wait_hours` with `pause()` - never mark files failed
   because the site is down. Up -> the file fails after its normal retries as before.
   Mock: `site.down` / `down_after`, mode `brokenfile`; tests in test_outage.py.
6. **State**: a file is only final once renamed from `.part` and recorded in the manifest, so a stop,
   crash or restart at any point is safe; the next sync resumes (`interrupted_job`).
7. **Politeness**: `Limiter` halves parallelism and doubles the delay on 429/503/timeouts/slow
   responses and recovers gradually. Downloads default to 2 at a time. Respect robots.txt by default.

## Publishing (GitHub: sireteanu/rom-sync)
- Claude pushes to `main` from this session; the user's Unraid server builds the image from GitHub
  (`unraid/docker-compose.yml`, Compose Manager, `pull_policy: build`) - after a push the user
  clicks Compose Up. On Windows the user can run `publish.bat` (commit + pull --rebase + push).
- Releases: **every push that changes the app is its own new version** (the Unraid server builds
  whatever is on `main`, so the running version must match a release). By default bump the
  last number (1.2.1 -> 1.2.2), also for small features; move to the next minor (1.3.0, 1.4.0)
  only once changes are significant - the user decides, so ask before a minor/major bump. Add a `## [x.y.z] - date` section to `CHANGELOG.md` (plain words,
  grouped New / Improved / Fixed / Changed), bump `version` in `pyproject.toml` and
  `src/lolroms_sync/__init__.py`, push; the `Release` workflow creates `v<version>` with that
  section as its text. Published changelog sections are only edited to correct them (errors, or
  details that must not be public); the workflow then updates that release's text to match.
  Pushes that only touch tooling/docs (workflows, CLAUDE.md) don't need a version. (This session can't call the
  releases API directly.) `docker.yml` (registry image) runs only when started by hand.
- `git fetch origin main` before pushing from a shallow clone.
- **Docs stay current with every change** (same commit): `README.md` is the complete reference
  for people and AI assistants (features, how it works, settings, options/env vars, files,
  API, code map, release process, lolroms facts); `docs/user-guide.md` teaches users step by step;
  `CHANGELOG.md` gets the version's entry. The GitHub repo's About text/topics can't be
  changed from this session (403) - give the user a `gh repo edit` command if it needs updating.

## Conventions
- Dependencies: httpx[http2], selectolax, fastapi, uvicorn (+ pytest/pyflakes for dev). Ask before adding more.
- Log through `logging.getLogger("dircrawl")` (the page's log and `rom-sync.log` read it). Never `print`.
- Every user-visible behaviour change gets a test in `tests/` against `MockSite`. If a real-site
  quirk is discovered, reproduce it in `tests/mock_site.py` first, then fix.
- **Tests and development never touch the real lolroms.com.** Only run the tool against the real
  site when the user asks, and never start a download of the real site unless they asked for it
  (it can be hundreds of GB).
- Keep saved settings working across versions: convert old keys instead of dropping a user's choices.
- Files Rom Sync writes for itself go through `.tmp` + `os.replace`.

## Gotchas
- `asyncio.Condition` must be created inside the running loop (Limiter is built in `Scanner.run`).
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

## Privacy
- Never put the owner's real server paths, share names, drive letters, time zone, IP addresses,
  user names or keys into the repository (code, docs, tests, changelog, commit messages). Use
  placeholder paths in docs and examples: `/path/to/roms`, `/path/to/appdata/rom-sync`,
  `D:\path\to\roms`, `America/New_York` (no real-looking server paths).
