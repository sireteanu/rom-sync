# Changelog

What changed in each Rom Sync release. The section for a version becomes the text of its GitHub
release. Newest first.

## [Unreleased]

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
