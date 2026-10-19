# Rom Sync

Rom Sync keeps a local copy of the [lolroms.com](https://lolroms.com/) folders you choose, and
keeps it up to date. It reads the whole site into a catalog (without downloading files), shows
every folder as a tree with tick boxes, and each sync downloads only what is new, incomplete or
changed. To show the synced folders in [RomM](https://github.com/rommapp/romm), use
[ROM Mapper](https://github.com/sireteanu/rom-mapper) (Rom Sync's RomM Mapper moved there in 1.3.5).

It runs as a web page: in Docker (made for Unraid) or on Windows.

- **New here?** Read the [user guide](docs/user-guide.md).
- **What changed?** See the [changelog](CHANGELOG.md) and the
  [releases](https://github.com/sireteanu/rom-sync/releases). The running version is shown next to
  the title in the web page.
- **Working on the code (people or AI assistants)?** This README is the full reference: every
  behaviour, setting, file and endpoint is described below. Coding rules and invariants are in
  [CLAUDE.md](CLAUDE.md).

---

## Contents

1. [Features](#features)
2. [Install](#install)
3. [How it works](#how-it-works)
4. [RomM](#romm)
5. [Settings reference](#settings-reference)
6. [Command-line options and environment variables](#command-line-options-and-environment-variables)
7. [Files on disk](#files-on-disk)
8. [HTTP API](#http-api)
9. [Code map](#code-map)
10. [Development, tests and releases](#development-tests-and-releases)
11. [Facts about lolroms.com this relies on](#facts-about-lolromscom-this-relies-on)

---

## Features

| Area | What it does |
|---|---|
| Catalog | **Scan site** reads every listing page of lolroms.com (about 330 folders, 117,000+ files) into a local database. File links are never requested while scanning. Sections linked only from the site's header bar (Arcade, Pokéhacks, Audio [FLAC], fixdats) are included. |
| Folder tree | Every folder with file count, total size, newest file date and how much is synced. Columns sort by clicking their header (ascending → descending → name order). Search box finds folders by name. |
| Saved as | A column in the folder tree with every folder's full path in the download folder (`/Apple/II`). Type another path (`/Apple II`) to put it there, or click **−date** on a dated folder. The arrow next to it sets where a folder's subfolders go (`/` = the base of the download folder). Everything below a folder follows it. While typing, RomM's platforms are suggested (`Sega 32X` -> `/sega32`), and a badge names the RomM platform a folder is saved under. |
| Renamed folders | When the site moves a folder to a newer set date (`Archimedes (2023…)` → `Archimedes (2026…)`), ticks and Saved as settings follow it, after a scan or by themselves during a sync. |
| Selection | Tri-state tick boxes. Ticking a folder includes everything below it, including folders the site adds later. A folder with files of its own *and* subfolders shows a **Files in this folder** row, so its own files can be ticked without its subfolders. Ticks are saved immediately. |
| Sync | Downloads only what's missing, half-finished (`.part`, resumed) or changed on the site (date or size in the listing differs). Unchanged files cost no network request. |
| Dates | Each saved file gets the date the website lists for it (12:00 local time). After a sync, each folder gets the date of its newest file. |
| Permissions | Downloaded files and new folders get mode **777** by default (setting). |
| Speed | Parallel downloads ramp up until the server pushes back, settle one below, re-probe every 2 minutes and remember the limit. Optional speed cap and free-space guard. |
| Site outages | When lolroms itself stops answering (5xx/Cloudflare 52x, timeouts, refused connections), a file that has failed 3 times because of it triggers a check of the site; if the site is down, downloads **wait** for it (re-checking every 30 s, doubling to every 10 min) instead of failing, and carry on when it's back. After "When the site is down, wait up to" (default 12 h) the sync pauses; the next sync continues. |
| Daily limit | If lolroms starts sending its "download limit" web page instead of files, downloads pause and continue at the next sync. |
| Progress | Activity panel (counts, speed, parallel downloads - click New, Updated, Resumed, Up to date or Failed to see those files), **This sync** box (total size, files left, time remaining), **Downloading** box (one progress bar per file), live log. |
| Activity log | **Activity log** (header) lists every file added, updated, resumed, deleted or failed, grouped by day, kept for a year. |
| Clean up | After each sync, lists the files in the download folder that aren't part of the sync any more (removed from the site, in folders you unticked, unneeded `.part` files). **Clean up** shows them grouped; you tick what goes and confirm before anything is deleted. |
| Schedule | Optional automatic sync every N hours and a full site scan every N days. |
| Reliability | A sync cut off by a restart, crash or reboot continues by itself at the next start. Power menu: **Restart** / **Shut down**. Starting a second copy just opens the running one. |
| Interface | Dark (default) or light theme, works on a phone (Activity, Log and Downloading sit under the folder tree), optional password (HTTP Basic), no external resources (works on a LAN without internet for the page itself). |

---

## Install

### Unraid (Docker Compose Manager) - recommended

The image is built on the server from this GitHub repository; no image registry is involved.

1. Install **Docker Compose Manager** from Apps.
2. Docker tab → Compose → **Add New Stack** `rom-sync` → Edit Stack → Compose File → paste
   [`unraid/docker-compose.yml`](unraid/docker-compose.yml) and adjust the paths:
   ```yaml
   services:
     rom-sync:
       build:
         context: https://github.com/sireteanu/rom-sync.git#main
       pull_policy: build            # every "Compose Up" rebuilds from the latest code
       image: rom-sync:latest
       container_name: rom-sync
       ports:
         - "8484:8484"               # change the first number for another port
       volumes:
         - /path/to/appdata/rom-sync/config:/config   # catalog, settings, ticks, log
         - /path/to/roms:/romm               # the folder that holds the downloads
       environment:
         - PUID=99
         - PGID=100
         - UMASK=000
         - TZ=America/New_York
         - LOLROMS_DATA=/romm/lolroms                   # downloads go here
         # - LOLROMS_PASSWORD=change-me
       labels:
         net.unraid.docker.webui: "http://[IP]:[PORT:8484]/"
       restart: on-failure           # restarts after a crash, stays off after "Shut down"
   ```
   A plain `- /path/to/roms:/data` mapping (and no `LOLROMS_DATA`) works too; the layout above
   keeps the downloads in a `lolroms` subfolder, which suits ROM Mapper's RomM links.
3. **Compose Up**, then turn on **Autostart** for the stack. Open `http://SERVER:8484/`.
4. To update: click **Compose Up** again (it fetches the latest code and rebuilds). Your catalog,
   ticks and downloads are kept.

Alternatives: [`docker/unraid-update.sh`](docker/unraid-update.sh) does the same with plain
`docker build` / `docker run`; [`docker-compose.yml`](docker-compose.yml) builds from a local
checkout; [`unraid/lolroms-sync.xml`](unraid/lolroms-sync.xml) is a Docker-tab template for an
image published to a registry (the `Docker image` workflow can publish one to ghcr.io / Docker Hub
when started by hand).

### Windows

1. Install Python 3.9+ (tick "Add python.exe to PATH").
2. Double-click **`start.bat`**. The first start creates `.venv` and installs the dependencies; the
   browser then opens `http://localhost:8484/`.
3. Set the download folder in Settings, or start with `start.bat --data D:\path\to\roms`.

Catalog and settings live in `config\` next to `start.bat`; the log is `config\rom-sync.log`.
`run.py` is an alternative launcher that keeps its window open on errors.

---

## How it works

### Scanning (building the catalog)

- A **full scan** starts at `https://lolroms.com/` and follows `?dir=` folder links breadth-first
  with up to 8 parallel requests, pacing itself (it halves parallelism and doubles the delay on
  429/503/timeouts or slow answers and recovers gradually). It respects `robots.txt`.
- Every listing row gives the file's name, link, **date** and **size** (see
  [facts](#facts-about-lolromscom-this-relies-on)). Files are recognised from the link alone
  (`?file=Folder%2FName.7z`); they are never requested during a scan.
- Only the file types chosen in Settings are counted (default **archives**: zip, 7z, rar, tar, gz…;
  "everything" adds iso, chd, ROM images…).
- After a complete scan, files and folders the site no longer lists are removed from the catalog
  (only below folders that were read successfully). Your downloaded files are never deleted by a
  scan; see [Clean up](#clean-up).
- A folder that couldn't be read is shown in red with the error; it doesn't stop the scan.

### Selection rules

- A tick is a rule on a folder and applies to everything below it, including folders added to the
  site later. Unticking a subfolder adds an exception.
- **Files in this folder** (shown as the first row of a folder that has files *and* subfolders) is
  a rule for only the files directly in that folder (stored as `"<folder>/."`).
- A box shows `-` (partial) when some things below it differ. Clicking a `-` box ticks everything.
  Ticking every subfolder (and its own files, if any) one by one turns the folder into a full tick.
- The header strip shows the total size of the selection, how much of it is synced and how much
  is left to download.

### Syncing

For every selected file (after re-reading the selected folders' listings, unless that setting is
off), `Downloader.plan()` compares the listing with the disk and with a manifest of what was
downloaded before:

| On disk | Action |
|---|---|
| Missing | **New**: download |
| `.part` file, or smaller than recorded | **Resume** from where it stopped (HTTP Range) |
| Listing date or size differs from when it was saved | **Update**: download again; the old copy is replaced only when the new one is complete |
| Matches | **Skip**, no request |
| Present but not in the manifest (copied in by hand) | **Adopt** if its size fits the listing, otherwise resume/update |

- Planning runs in a worker thread (`plan_all()` in `Engine._sync`; the manifest's SQLite connection
  allows it because no download runs yet) and asks the disk once per file (`os.stat`, passed to
  `sync.plan()`, `stamp_file()` and `_set_mode()`); permissions and dates are only written when they
  differ. The Clean up check (`walk` + `find`) also runs in a worker thread.
- **Saved as** (`folder_map.py`): two settings keyed by site folder (case-insensitive), changed only
  through their endpoints. `folder_places` `{folder: place}`: a name (renamed in place), a path (below
  its parent's place on disk) or `/path` (from the download folder). `folder_kids` `{folder: place}`:
  where that folder's subfolders go, each under its own name (`/` = the download folder itself);
  the folder's own files stay with it, and a subfolder's own place wins. Places are resolved top-down
  (`FolderMap.map_parts`), so everything below a folder follows it; file names never change.
  The column shows `/` + `disk_path` and sends full paths; `FolderMap.as_typed()` stores them
  relative to where the folder sits anyway (`base_of`): the default path clears the setting, a path
  below the base becomes a rename/relative path (it follows a moved parent), anything else stays
  `/path`. The field's `<datalist>` comes from `static/romm-platforms.json` (RomM's Supported
  Platforms table: platform name -> RomM folder name, CC0); refresh it with
  `scripts/update_romm_platforms.py` after cloning `rommapp/docs`.
  `UrlRules.rel_path(url, folder_map)` applies it when a listing is read; saving a setting (and every
  start) recomputes all catalog paths (`Catalog.remap()`); files whose place changed are planned afresh
  by the next sync (a copy already at the new place is adopted by size). Clean up maps catalog folders
  to disk with the same map; several site folders may share one disk folder. Folder map rules and
  names from 1.3.10-1.4.2 (`folder_map`, `folder_names`) are converted once at start
  (`from_old_settings`).
- **Renamed folders** (`Catalog.follow_renames()`, run after every finished scan): a ticked folder the
  site no longer lists, whose parent now lists a folder with the same name apart from the set date
  (either form, see the lolroms facts below; the newest if there are several), gets its selection rules moved
  there; its Saved as settings follow (`Engine._follow_map_renames`). During
  the pre-sync refresh, a ticked dated folder that can't be read makes the engine read its parent's
  listing only (`Scanner.run(parent, recurse=False)`), follow the rename, and refresh the new folder.
- Files are written as `<name>.part` and renamed when complete. A web page received instead of a
  file is rejected, and archive headers are checked (7z/zip/rar…).
- **Dates**: lolroms sends no `Last-Modified` header, so the listing date is the only date; files
  get it at 12:00 local time (`TZ`). Already-present files are re-dated without downloading. After
  each sync, every folder that was touched (and its parents) gets the date of the newest item in
  it. If the server refuses to change dates, the log says so once.
- **Permissions**: with the File permissions setting (default `777`), each saved or checked file is
  `chmod`-ed, and new folders get the same mode plus the "enter" bit where reading is allowed.
- **Parallel downloads** (`DownloadGate`): start at 2 (or the limit remembered for the site), add one
  while every slot is busy, and on push-back (HTTP 429/503, refused connections) set the ceiling one
  below; a level that worked before needs two push-backs within 10 s to be lowered; every 2 minutes
  it tries one more. Pushed-back files go back in the queue without using their retries. Setting
  "Fixed number" disables this.
- **Site outages** (`Downloader.wait_for_site`): errors caused by the site (HTTP 500/502/503/504/52x,
  timeouts, refused or dropped connections) count per file; after 3 of them the downloader asks the
  site root (`site_up()`: any answer below 500 = up). If it answers, the file keeps its normal retries
  and fails after them as before (the problem is that file). If not, the sync enters **outage**:
  the file goes back in the queue without using its retries, every worker waits (one checks, the
  others wait; no download slots are held, the parallel-downloads ramp is frozen), checks at 30 s,
  then doubling to every 10 min. When the site answers, downloads continue (`.part` files resume).
  After `outage_wait_hours` (default 12; 0 = never wait) the sync pauses with nothing marked
  failed. Status `sync.outage` = `{since, error, checks, next_check, gives_up}`; the page shows a
  notice and the pill "Waiting for site".
- **Daily limit**: when the site answers with an HTML page instead of a file while nothing else is
  downloading, downloads pause ("Paused: …") and stay queued for the next sync.
- **Free space**: downloads pause before the disk would drop below "Keep free on disk".
- **Interrupted syncs**: while a sync runs, `interrupted_job` is stored; if the app stops without the
  sync ending (restart, crash, reboot, Shut down), the sync starts again at the next start and
  resumes `.part` files. Stop (button) and a normal finish clear it.
- **Schedule**: with "Sync automatically every N hours", the next run is N hours after the last sync
  finished; it becomes a scan+sync when the last full scan is older than "Full site scan every N days".

### Clean up

`cleanup.py`. After every sync that finishes (not stopped or paused), and whenever the **Clean up**
dialog opens, Rom Sync walks the download folder and judges each file against the catalog:

| Group | Meaning | Ticked by default |
|---|---|---|
| **No longer on the site** (`gone`) | Not in the listing of its folder, which was read without errors; or its folder is gone and a full scan has finished since | yes |
| **Unfinished downloads that aren't needed** (`part`) | A `.part` file whose finished file isn't wanted (wanted ones are resumed by the next sync) | yes |
| **Not ticked any more** (`unticked`) | Still on the site, but its folder isn't ticked or its type isn't synced | no |

- Never listed: folders Rom Sync hasn't read or couldn't read (red in the tree), anything before
  the first full scan for folders the catalog doesn't know, dot-files (including the manifest
  `.dir_crawler.db`), `Thumbs.db`/`desktop.ini`, and symlinks (never followed or deleted).
- The Activity panel says how many files/bytes were found ("Review"); the log has a line too.
- Deleting needs two clicks: **Delete selected…** then **Delete N files** in the confirmation bar.
  The API refuses a delete without `confirm: true` and while a scan or sync runs (409). Only paths
  that are still leftovers when the delete runs are removed (anything that became part of the sync
  again, or a path outside the download folder, is skipped). Folders left empty are removed (never
  the download folder itself), the manifest forgets the files, and the catalog stops counting them
  as synced, so ticking them again downloads them again.

### Interface

- Header: title with **version** (links to its release notes), job pill, **Scan site**, **Sync
  selected**, **Stop**, **Activity log**, and a menu button (☰) holding **Settings**,
  **Light/Dark theme**, **Restart** and **Shut down**.
- The Activity counters New / Updated / Resumed / Up to date / Failed are buttons: each opens the
  files behind it for the current or last sync (`GET /api/sync/files`). "Up to date" comes from the
  catalog (files the sync checked without downloading); the others from the change history.
- **Activity log**: one row per day (newest first) with counts and sizes per kind; opening a day
  loads its files grouped by kind (`GET /api/changes/days`, `GET /api/changes?day=`).
- Storage strip, folder tree, Activity (job line, This sync box, counters, Clean up), Log, Downloading.
- The page polls `/api/status` every second while busy, every 4 s when idle, and not at all while
  its browser tab is hidden (it refreshes as soon as you come back). Replies over 1 KB are
  gzip-compressed (folder tree, search, file lists, the page's script).

---

## RomM

The RomM Mapper (platform links next to the download folder and a generated `config.yml`) moved to
[ROM Mapper](https://github.com/sireteanu/rom-mapper) in 1.3.5, which can also map other folders
besides Rom Sync's downloads. What is left here:

- Platform links Rom Sync created stay where they are; ROM Mapper manages links of the same form.
- Your folder choices stay in `<config>/romm-mapper.json` (written from the catalog at the first
  start of 1.3.5 if that file was missing). Copy it into ROM Mapper's config folder to keep them.
- The catalog's old `romm` setting is no longer read.

---

## Settings reference

Stored in the catalog (`settings` table); changed in the Settings dialog or `POST /api/settings`.

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | from `--data` | Download folder (locked to `LOLROMS_DATA` in Docker) |
| `types` | `archives` | `archives`, `all`, or a list like `.7z,.zip` |
| `parallel` | `auto` | `auto` or a fixed number of parallel downloads |
| `parallel_max` | `16` | Upper limit for `auto` |
| `max_rate` | `""` | Overall speed limit, e.g. `10M` (empty = unlimited) |
| `min_free` | `2G` | Pause downloads before free disk space drops below this |
| `file_mode` | `777` | Permissions for downloaded files and new folders (octal; empty = system default) |
| `sync_every_hours` | `0` | Automatic sync interval (0 = off) |
| `full_scan_every_days` | `7` | With the schedule on: full scan when the last one is older |
| `refresh_before_sync` | `true` | Re-read the ticked folders' listings before each sync |
| `resume_interrupted` | `true` | Continue an unfinished sync when the app starts |
| `check_remote` | `false` | Ask the server about every existing file (exact, one request per file) |
| `outage_wait_hours` | `12` | How long a sync waits for the site when it's down before pausing (0 = don't wait; files then fail after their retries) |
| `interrupted_job` | `""` | Internal: set while a sync runs |
| `folder_places` | `{}` | Saved as: `{site folder: place}`; changed only with `POST /api/folder-place` |
| `folder_kids` | `{}` | Where a folder's subfolders go: `{site folder: place}`; changed only with `POST /api/folder-kids` |
| `folder_map`, `folder_names` | | From 1.3.10-1.4.2; converted to `folder_places` at start, then emptied |

---

## Command-line options and environment variables

`python -m lolroms_sync` (also `start.bat`, `run.py`, or `lolroms-sync` when installed):

| Option | Environment variable | Default | Meaning |
|---|---|---|---|
| `--data DIR` | `LOLROMS_DATA` | `downloads` (`/data` in Docker) | Download folder |
| `--config DIR` | `LOLROMS_CONFIG` | `config` (`/config` in Docker) | Catalog, settings, log |
| `--host` | `LOLROMS_HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Listen address |
| `--port` | `LOLROMS_PORT` | `8484` | Port |
| `--password` | `LOLROMS_PASSWORD` | none | Ask for this password (HTTP Basic, any user name); `/api/health` stays open |
| `--lock-data` | `LOLROMS_DATA_LOCKED=1` | off (on in Docker) | Download folder can't be changed in the page |
| `--no-browser` | `LOLROMS_NO_BROWSER=1` | off (on in Docker) | Don't open a browser tab |
| `--site URL` | `LOLROMS_SITE` | `https://lolroms.com/` | Hidden; for tests |

Docker only: `PUID`/`PGID` (default 99/100 = Unraid nobody/users; the entrypoint drops to this user
with `setpriv`), `UMASK` (default `000`), `TZ` (file dates and schedule). If the port is busy and
answers like Rom Sync, a new start just opens the running copy; otherwise it explains the conflict.

---

## Files on disk

| Where | What |
|---|---|
| `<config>/catalog.db` | SQLite (WAL): `dirs`, `files` (listing date/size, local state), `selection` (rules), `settings`, `scans`, `history`, `changes` (per-file change history: time, local day, sync id = its start time, kind new/updated/resumed/failed/deleted, path, size, note; pruned after 365 days) |
| `<config>/rom-sync.log` | Log (rotating, 2 MB × 3). Each start logs the catalog path, size and contents (folders, files, ticks, last full scan) and the download folder's top-level items; a warning when the catalog is new or empty but the download folder isn't (wrong `/config` mapping) |
| `<config>/romm-mapper.json` | RomM Mapper choices from before 1.3.5, kept for copying to ROM Mapper |
| `<data>/<site path>` | Synced files, same folder structure as the site (`Nintendo/Super Famicom/…`), except where Saved as puts a folder elsewhere |
| `<data>/.dir_crawler.db` | Manifest: what the listing said for each saved file, Last-Modified/ETag, per-site parallel limit |
| `<data>/…/*.part` | Unfinished downloads (resumed later) |

Copy `catalog.db` to move your scan and ticks to another machine; copy the download
folder including `.dir_crawler.db` to avoid downloading again (files are adopted otherwise).

---

## HTTP API

All JSON; the page uses nothing else.

| Method & path | Purpose |
|---|---|
| `GET /api/status?log_since=N` | Version, busy, job, catalog totals, selection totals (cached until the tree or ticks change), scan / sync progress (incl. `bytes_planned`, `bytes_left`, `files_left`, `eta`, `active` downloads), `leftovers` (Clean up summary from the last check, or null), next run, new log lines |
| `GET /api/tree?path=P` | Folder node and its children (counts, sizes, newest, synced, `state`, `own_*`, `own_state`, `place`, `custom`, `disk_path`, `moved`, `kids_to` for the Saved as column) |
| `GET /api/search?q=` | Folders matching a name |
| `GET /api/files?path=P` | Files directly in a folder with status |
| `POST /api/select` `{path, include}` | Tick/untick a folder; `path` `"<folder>/."` = its own files only |
| `POST /api/selection/clear` | Untick everything |
| `POST /api/scan` `{root}` · `POST /api/sync` · `POST /api/stop` | Jobs (409 when busy) |
| `GET/POST /api/settings` | Read / change settings (400 with a message on invalid values) |
| `POST /api/folder-place` `{path, place}` | Saved as: where this folder goes (`""` = as on the site) -> `{path, place, moved}`; 400 on a bad place, 409 while busy |
| `POST /api/folder-kids` `{path, place}` | Where this folder's subfolders go (`"/"` = the download folder, `""` = inside it) -> same reply |
| `GET /api/history` | Last jobs |
| `GET /api/sync/files?kind=` | Files behind an Activity counter for the current/last sync: `kind` = `new`, `updated`, `resumed`, `uptodate`, `failed` -> `{total, files: [{t, kind, path, size, note}], job}` (max 2000) |
| `GET /api/changes/days` | Change history per day: `[{day, counts: {kind: n}, bytes: {kind: b}}]`, newest first |
| `GET /api/changes?day=&kind=` | Changes (newest first, max 2000) with `total` |
| `GET /api/cleanup` | Check the download folder now: `files` `[{path, size, reason, url?}]` (`reason` `gone`/`part`/`unticked`) and `summary` |
| `POST /api/cleanup/delete` `{paths, confirm: true}` | Delete those leftovers -> `{deleted, bytes, errors, skipped, summary}`; 400 without `confirm`, 409 while busy |
| `POST /api/restart` · `POST /api/shutdown` | Restart (re-execs the program) / end it |
| `GET /api/health` | `{"ok": true}` (Docker health check; no password) |

---

## Code map

```
src/lolroms_sync/            the web app
  __main__.py   options / env vars, port check, log file, uvicorn, restart (re-exec) / shutdown
  web.py        FastAPI routes and Basic auth
  engine.py     one job at a time (scan | sync | scan+sync), progress, ETA, LogBuffer, scheduler,
                interrupted-sync resume, folder dating and the Clean up check after a sync
  catalog.py    SQLite catalog; tree() with recursive totals (cached); selection rules
                (effective / effective_files / state_of / select / _fold)
  scanner.py    reads listing pages into the catalog; header_dirs() for header-only sections; prune
  cleanup.py    Clean up: walk the download folder, judge files (gone/part/unticked), delete confirmed ones
  folder_map.py Saved as: where each site folder (and its subfolders) goes on disk
  static/       index.html, app.css, app.js - no build step, no CDN
  core/         the sync engine (from lolroms-downloader; its command-line tool was removed in 1.3.0)
    download.py   Downloader: plan() + download(), .part/resume, dates, permissions, run_item()
    sync.py       FileInfo, Manifest, plan(), site_timestamp(), stamp_file(), stamp_folders()
    extract.py    LinkExtractor (links + per-file date/size from listing rows)
    limits.py     Limiter (page pacing), DownloadGate (auto parallel downloads), RateLimit
    urls.py / config.py / constants.py / util.py
tests/          pytest against tests/mock_site.py (a local copy of lolroms' markup); never the real site
                test_app (API, selection, settings), test_engine (downloading, resume,
                updates, dates, parallel downloads, listing parsing), test_cleanup, test_changes, test_outage, test_units
docker/         entrypoint.sh (PUID/PGID/UMASK), unraid-update.sh
unraid/         docker-compose.yml (Compose Manager), lolroms-sync.xml (Docker-tab template)
.github/workflows/  tests.yml (pytest + pyflakes on every push, Python 3.9 and 3.12),
                release.yml (GitHub release per version), docker.yml (registry image, manual)
Dockerfile      installs the dependencies in their own layer (rebuilt only when they change)
start.bat, run.py, publish.bat   launchers / helpers
scripts/update_romm_platforms.py  refreshes static/romm-platforms.json from RomM's docs
```

---

## Development, tests and releases

```
pip install -e ".[dev]"
python -m pytest            # ~95 tests, ~1.5 min, all against the in-process mock site
python -m pyflakes src tests
python run.py --data downloads --config config
```

- Tests and development never contact the real lolroms.com. Real-site quirks are reproduced in
  `tests/mock_site.py` first (its markup copies the live site's).
- Every user-visible change gets a test, a line in `CHANGELOG.md`, and updates to this README and
  the [user guide](docs/user-guide.md).
- **Versions**: every change to the app is its own version. Small changes bump the last number
  (1.2.6 → 1.2.7); the middle number only for significant changes (decided by the owner). Bump
  `pyproject.toml` and `src/lolroms_sync/__init__.py`, add `## [x.y.z] - date` to the changelog,
  push to `main`: the `Release` workflow publishes GitHub release `vx.y.z` with that section as its
  text. A published section is only edited to correct it; the workflow then updates the release text.
- The `Tests` workflow runs the suite and pyflakes on every push.
- Unraid picks up `main` on **Compose Up**. On Windows, `publish.bat` commits, pulls and pushes.

---

## Facts about lolroms.com this relies on

Verified in a browser (October 2026). If the site changes, these are the first things to check.

- Folders: `https://lolroms.com/?dir=Nintendo%2FSuper+Famicom`; files: `?file=Folder%2FName.7z` (the
  extension is in the query value). `&sort=name|date|size` variants are the same listing.
- Listing row: `<div class="row"><a href="?file=…">📄 Name</a><span class="file-meta">2025-11-22 • 1.16 KB</span></div>`.
  The link text is never used (names contain dates).
- Ignored regions: `.sortbar`, `.back` ("⬅ Back"), `<nav>`, header bars - except that a full scan
  reads one-level `?dir=` links from the root page's header (Arcade, Pokéhacks, Audio [FLAC], fixdats).
- File responses: `Content-Length`, `Accept-Ranges: bytes`, `Content-Disposition`, served through
  Cloudflare; **no `Last-Modified` or `ETag`**.
- When the daily download limit is reached the site serves an HTML page instead of the file.
- Many folders carry the dump set's date at the end of the name, in two forms:
  `Archimedes (20231029-220453)` (` (YYYYMMDD-HHMMSS)`) and
  `PC Engine CD & TurboGrafx CD (2026-10-14 05-51-53)` (` (YYYY-MM-DD HH-MM-SS)`); seen October 2026.
  `catalog.SET_DATE` and `SET_DATE` in app.js match both.
