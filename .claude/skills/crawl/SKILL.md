---
name: crawl
description: List the folders of a directory-listing site (e.g. a lolroms.com ?dir= URL) that contain archive files, and summarise what was found. Use when the user wants to see what is on the site or in a folder, without downloading.
argument-hint: <start-url> [extra dir-crawler options]
allowed-tools: Bash(python dir-crawler.py *), Bash(py dir-crawler.py *), Read
---

List the folders that contain archives, starting from: $ARGUMENTS

1. Run from the project root (use `py` instead of `python` if `python` is not found on Windows):
   `python dir-crawler.py <start-url> --list-files --plain -q -o crawl-results/directories.csv [extra options]`
   Quote the URL. If the user gave extra options, pass them through unchanged.
   If it exits with code 2 because an unfinished crawl exists, ask the user whether to `--resume` or `--fresh`.
2. Read `crawl-results/directories.csv` and `crawl-results/directories_files.csv`
   (and `crawl-results/directories_errors.csv` if it exists).
3. Reply with:
   - how many folders contain archives, and the total number of archive files
   - the 10 largest folders by file count (name and count)
   - any failed pages, with the reason
   - the paths of the output files
4. Do not start a download. If the user wants the files, suggest `/download <url> <folder>`.
