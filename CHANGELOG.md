# Changelog

What changed in each Rom Sync release. The section for a version becomes the text of its GitHub
release. Newest first.

## [Unreleased]

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
