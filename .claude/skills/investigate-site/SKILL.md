---
name: investigate-site
description: Work out why the crawler misbehaves on a site (wrong folders, missed files, crawl escaping scope, loops, slowness) or adapt it to a new directory-listing site. Use when a crawl gives unexpected results or the user wants to support a different site.
argument-hint: <url of a listing page that behaves wrongly>
allowed-tools: WebFetch, Read, Grep, Glob, Bash(python -m pytest *), Bash(python dir-crawler.py *)
---

Investigate: $ARGUMENTS

1. **Look at the real page structure.** Fetch one listing page and one deeper sub-folder page. Record:
   - how folder links are formed (path `/a/b/`, or query `?dir=a%2Fb`)
   - how file links are formed and where they are hosted (path extension or `?file=...ext`)
   - the class or id names of the header, nav, sidebar, sort bar and back link, plus any pagination
   - the logo / home link
2. **Reproduce small.** Run the tool against the URL with `--max-pages 20 -v --plain` and compare
   the log with step 1. Typical causes are a file-link format that isn't recognised, a chrome
   region that isn't ignored (use `--ignore-class` / `--nav-selector`), scope params that differ
   from the start URL (`--keep-params`), or a new file extension (`--download-ext`).
3. **Fix with a test first.** Model the quirk in `tests/mock_site.py` (or a new fixture), add a
   failing test, then change `urls.py`, `extract.py` or `constants.py`. Keep the invariants in
   `CLAUDE.md`, above all that file links are never fetched during a listing crawl.
4. Run `python -m pytest` and report what was wrong, what changed, and the command the user should run now.

Do not run large crawls or any download of the real site while investigating.
