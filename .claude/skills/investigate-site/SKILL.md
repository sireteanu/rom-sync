---
name: investigate-site
description: Work out why Rom Sync reads lolroms.com wrongly (missing folders or files, wrong dates or sizes, a scan leaving its folder, loops, slowness) or adapt it to a change in the site's markup. Use when a scan or sync gives unexpected results.
argument-hint: <url of a listing page that behaves wrongly>
allowed-tools: WebFetch, Read, Grep, Glob, Bash(python -m pytest *)
---

Investigate: $ARGUMENTS

1. **Look at the real page structure.** Fetch one listing page and one deeper sub-folder page
   (ask the user first - development never touches the real site unasked). Record:
   - how folder links are formed (`?dir=a%2Fb`) and which sections are only linked from the header
   - how file links are formed (`?file=...ext`) and the markup of a file row (date, size)
   - the class or id names of the header, nav, sidebar, sort bar and back link, plus any pagination
2. **Reproduce in the mock.** Copy the quirk into `tests/mock_site.py` (the mock must match the live
   markup, see CLAUDE.md "Gotchas") and add a failing test in `tests/test_engine.py` or
   `tests/test_app.py` that goes through the web API.
3. **Fix.** Change `src/lolroms_sync/core/extract.py`, `core/urls.py`, `core/constants.py` or
   `scanner.py`. Keep the invariants in `CLAUDE.md`, above all that file links are never fetched
   while reading listings.
4. Run `python -m pytest` and `python -m pyflakes src tests`, then report what was wrong and what changed.

Never start a full scan or any download of the real site while investigating.
