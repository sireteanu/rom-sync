# Changelog

What changed in each Rom Sync release. The section for a version becomes the text of its GitHub
release. Newest first.

## [Unreleased]

## [1.2.7] - 2026-07-21

### New
- **RomM platform links**: in the RomM dialog, choose **Platform links in one folder**. Rom Sync
  then keeps one folder link per RomM platform next to its download folder - for example
  `snes` -> `lolroms/Nintendo/Super Famicom` - and updates them after every sync. Map that one folder
  as RomM's roms folder (for example `/path/to/roms`); no per-platform Docker mounts are
  needed. The generated `config.yml` tells RomM to skip the download folder itself. Links are
  relative, so they work inside RomM's container; real folders and links Rom Sync didn't make are
  never touched.

### Changed
- The Unraid compose file now maps `/path/to/roms` to `/romm` and downloads into
  `/romm/lolroms`, so the platform links can sit next to the downloads.

## [1.2.6] - 2026-07-18

### Fixed
- **Files directly in a folder can be ticked on their own.** A folder that has both files and
  subfolders (like Nintendo/Super Famicom) now shows a **Files in this folder** row first. Tick it to
  sync those files without ticking every subfolder. Before, ticking only some subfolders left the
  folder's own files out, with no way to add them except ticking the whole folder.
- Ticking a folder's files and all of its subfolders one by one now shows the folder as fully
  ticked.

## [1.2.5] - 2026-07-16

### New
- **RomM `{category}` support**: the generated `config.yml` now uses RomM's `{category}` and
  `{subcategory}` folder levels (for example `roms/{platform}/{category}/{game}`) instead of listing
  every subfolder by name. Subfolders the site adds later are picked up without changing the config.
  Subfolders that are another platform or not games are still kept out. Where a wildcard would read
  the wrong folders, that platform keeps exact folder names automatically.
- RomM dialog option **Subfolders in config.yml**: `{category}` levels (default) or exact folder
  names (the previous behaviour).

## [1.2.4] - 2026-07-11

### New
- The version you're running is shown next to "Rom Sync" at the top of the page. Click it to see
  what changed in that version.

## [1.2.3] - 2026-07-08

### New
- **File permissions** setting (Settings): downloaded files and the folders Rom Sync creates get
  these permissions. The default is **777**, so everyone on the share can read and change them.
  Leave it empty to keep the system default. Files you already have are updated at the next sync.

### Fixed
- **Folders now show the website's dates too**: after a sync, each folder gets the date of the
  newest file inside it, instead of the day it was downloaded.
- If the server refuses to change file dates, the log now says so (once) instead of failing
  silently.

## [1.2.2] - 2026-07-05

### New
- **Shut down**: the restart button is now a power menu with **Restart** and **Shut down**.
  Shutting down stops Rom Sync until you start it again (start.bat, or Start / Compose Up on
  Unraid); a sync that was running continues at the next start.

### Changed
- The Unraid compose file now uses `restart: on-failure`, so the container still restarts after a
  crash but stays off after **Shut down**. Turn on **Autostart** for the stack in Compose Manager
  so Rom Sync starts with the server.

## [1.2.1] - 2026-07-04

### New
- **Downloading** box under the log: every file being downloaded right now, with its folder, a
  progress bar, percentage, size, speed and time left. It appears while a sync runs.

### Improved
- The Activity box stays short during big syncs (the file list moved to the new box), and a paused
  sync's reason is shown there on its own line.
- "Parallel" no longer shows command-line wording ("at --dl-max" now reads "at your upper limit").

## [1.2.0] - 2026-07-01

### New
- **RomM library** (RomM button): maps every lolroms folder to a RomM platform with built-in rules
  you can change, and writes read-only mounts for RomM's container (docker compose or Unraid Extra
  Parameters) plus a matching `config.yml`. Nothing is moved, so syncing keeps working. Includes a
  searchable list of every RomM platform with names and makers.
- **Restart button** in the header. A sync that was running continues afterwards.
- **Interrupted syncs continue by themselves** after a restart, crash or reboot (Settings can turn
  this off). Folder ticks were already saved as you click.
- **Runs on Unraid** with Compose Manager: the image is built on the server straight from GitHub
  (`unraid/docker-compose.yml`). `publish.bat` sends changes from Windows to GitHub.

### Improved
- Starting Rom Sync while it already runs opens the running copy instead of failing silently; a port
  used by another program is explained.
- Everything is also logged to `rom-sync.log` in the config folder.
- `run.py` keeps its window open on errors and uses the project's own Python when needed.

## [1.1.0] - 2026-06-22

### New
- **Arcade** support: Arcade, Pokéhacks, Audio [FLAC] and fixdats, which lolroms only links from
  its header bar, are now part of a full scan. Run **Scan site** once to pick them up.
- **Sortable columns**: click Folder, Files, Size, Updated or Synced for ascending, descending, or
  back to name order.
- **This sync** box under Activity: total size of the running sync, progress, files left and time
  remaining.

### Fixed
- Ticking every subfolder of a folder now shows a full tick on the folder instead of "-", and
  clicking a "-" box ticks the whole folder.

### Changed
- Renamed to **Rom Sync**.

## [1.0.0] - 2026-06-22

- First release: scans the whole of lolroms.com into a catalog, shows it as a folder tree with tick
  boxes, and keeps the ticked folders in sync - only new, incomplete or changed files are
  downloaded, file dates match the website, and parallel downloads tune themselves to what the
  server allows. Dark interface, scheduled syncs, optional password, Windows and Docker.
