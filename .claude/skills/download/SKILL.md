---
name: download
description: Download or re-sync every archive under a directory-listing URL into a local folder that mirrors the site's structure. Only new, incomplete or changed files are fetched.
argument-hint: <start-url> <folder> [extra dir-crawler options]
disable-model-invocation: true
allowed-tools: Bash(python dir-crawler.py *), Bash(py dir-crawler.py *), Read
---

Download the archives. Arguments: $ARGUMENTS ($0 is the start URL, $1 is the destination folder).

1. If the start URL is a site root or a very broad folder, warn that it may be hundreds of GB.
   Offer `--list-files` first to see the size of the job. Continue only if the user confirms.
2. Run from the project root (use `py` instead of `python` if `python` is not found on Windows):
   `python dir-crawler.py "$0" --download "$1" --plain -o "$1/_crawl/directories.csv" [extra options]`
   If a previous run for the same URL exists, the tool exits with code 2. In that case, re-run with
   `--resume`, which skips finished files and resumes `.part` files.
   This can take a long time, so run it in the background and check the log periodically.
   Running it again later is a sync: only new, incomplete or changed files are fetched. For ongoing
   syncing, suggest `--watch 6h`. Parallel downloads tune themselves; don't pass `--dl-workers`
   unless the user asks.
3. When it finishes, read `$1/_crawl/directories_files.csv` and `directories_errors.csv` and report:
   - the counts of new, updated, resumed and up-to-date files, and the size downloaded
   - the number of parallel downloads the server allowed (shown in the summary)
   - failures grouped by reason. If downloads were paused because the site sent a web page instead
     of files, that is usually its daily download limit; tell the user to run the same command with
     `--resume` later.
   - whether anything is still queued, and the exact command to resume.
4. Never delete downloaded files or `.part` files unless the user asks.
