"""lolroms' plain folder and file links (November 2026) and moving catalogs over to them."""
from __future__ import annotations

import sqlite3
import urllib.parse

from conftest import EXPECTED_ATARI, file_status, on_disk, scan, select, sync, tree

from lolroms_sync.core.config import Config
from lolroms_sync.scanner import PathRules, dir_of, header_dirs, shown_dir


def _log(c):
    return " ".join(l["msg"] for l in c.get("/api/status", params={"log_since": 0}).json()["log"])


def test_folder_and_file_links_are_plain_paths():
    assert dir_of("https://lolroms.com/Nintendo/Virtual%20Boy") == "Nintendo/Virtual Boy"
    assert dir_of("https://lolroms.com/?dir=Nintendo%2FVirtual+Boy") == "Nintendo/Virtual Boy"   # old catalogs
    assert dir_of("https://lolroms.com/") == ""
    r = PathRules(Config(start_url="https://lolroms.com/Nintendo/Virtual%20Boy", keep_params=frozenset()),
                  "Nintendo/Virtual Boy")
    # one spelling for every link: ?sort= dropped, segments percent-encoded the way the site does
    assert r.canonical("https://lolroms.com/Nintendo/Virtual%20Boy?sort=date") == \
        "https://lolroms.com/Nintendo/Virtual%20Boy"
    assert r.canonical("https://lolroms.com/Pokéhacks") == "https://lolroms.com/Pok%C3%A9hacks"
    assert r.canonical("https://lolroms.com/A/Zero (Japan, USA).7z") == \
        "https://lolroms.com/A/Zero%20%28Japan%2C%20USA%29.7z"
    # a folder scan stays in its folder: not the parent, not a sibling that starts the same
    assert r.in_scope("https://lolroms.com/Nintendo/Virtual%20Boy/T-En")
    assert not r.in_scope("https://lolroms.com/Nintendo")
    assert not r.in_scope("https://lolroms.com/Nintendo/Virtual%20Boy%20Extra")


def test_header_sections_are_found_but_not_its_files():
    body = ('<nav><a href="/" class="nav-item">Home</a><a href="/Arcade" class="nav-item">Arcade</a>'
            '<a href="/Pokéhacks" class="nav-item">PokéHacks</a><a href="/DATs" class="nav-item">DATs</a>'
            '<a href="/bulk_download.py" download="" class="nav-item">Bulk</a>'
            '<a href="/rss.xml" class="nav-item">RSS</a></nav>'
            '<div class="list"><div class="row"><a href="/Acorn">Acorn</a></div></div>').encode()
    r = PathRules(Config(start_url="https://lolroms.com/", keep_params=frozenset()), "")
    found = {dir_of(u) for u in header_dirs(body, "https://lolroms.com/", r)}
    assert found == {"Arcade", "Pokéhacks", "DATs", "Acorn"}
    assert shown_dir(body) == ""                                  # the root listing: no breadcrumb


def test_old_folder_links_are_not_trusted(site, app_factory):
    """The site answers an old ?dir= link, or a folder it doesn't have, with its root listing."""
    c = app_factory(site)
    scan(c)
    site.rename_dir("Atari/2600", "Atari/VCS")
    scan(c, "Atari/2600")
    assert "isn't on the site any more" in _log(c)
    assert "Adventure (USA).7z" in file_status(c, "Atari/2600")   # nothing dropped because of it


def test_catalog_with_old_links_is_moved_over_without_downloading_again(site, app_factory, tmp_path):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    assert on_disk(c.data) == EXPECTED_ATARI
    c.__exit__(None, None, None)
    # what a catalog from before November 2026 holds: ?file= / ?dir= links
    db = sqlite3.connect(tmp_path / "config" / "catalog.db")
    for (url,) in list(db.execute("SELECT url FROM files")):
        p = urllib.parse.urlsplit(url)
        old = f"{p.scheme}://{p.netloc}/?file=" + urllib.parse.quote_plus(urllib.parse.unquote(p.path).strip("/"))
        db.execute("UPDATE files SET url=? WHERE url=?", (old, url))
    for path, url in list(db.execute("SELECT path, url FROM dirs WHERE url IS NOT NULL")):
        db.execute("UPDATE dirs SET url=? WHERE path=?", (site.url + "/?dir=" + urllib.parse.quote_plus(path), path))
    db.commit()
    db.close()
    site.reset_log()
    c = app_factory(site)
    assert "catalog entries updated" in _log(c)
    db = sqlite3.connect(tmp_path / "config" / "catalog.db")
    assert not db.execute("SELECT COUNT(*) FROM files WHERE url LIKE '%file=%'").fetchone()[0]
    db.close()
    assert tree(c, "Atari")["node"]["synced"] == 7                 # still counted as synced
    sync(c)
    assert site.file_requests() == []                               # nothing downloaded again
    assert set(file_status(c, "Atari/2600").values()) == {"up to date"}


def test_files_copied_in_by_hand_are_checked_against_the_sites_size(site, app_factory):
    """The listing shows no sizes, so a file Rom Sync didn't download is compared with the size the
    site gives for it (HEAD) before it counts as synced."""
    c = app_factory(site)
    scan(c)
    (c.data / "Atari/2600").mkdir(parents=True)
    adv, pit = "Atari/2600/Adventure (USA).7z", "Atari/2600/Pitfall! (USA).7z"
    (c.data / adv).write_bytes(site.content(adv))
    (c.data / pit).write_bytes(site.content(pit)[:10_000])          # cut short
    select(c, "Atari/2600")
    sync(c)
    status = file_status(c, "Atari/2600")
    assert status["Adventure (USA).7z"] == "already in folder"
    assert status["Pitfall! (USA).7z"] == "resumed"
    assert (c.data / pit).read_bytes() == site.content(pit)
    assert len(site.heads) == 2
    assert [r for p, r in site.file_requests() if "Pitfall" in p] == ["bytes=10000-"]
    site.reset_log()
    site.heads.clear()
    sync(c)
    assert site.file_requests() == [] and site.heads == []           # known now: no more questions


def test_folder_sizes_come_from_downloads_when_the_site_lists_none(site, app_factory):
    c = app_factory(site)
    scan(c)
    assert tree(c, "Atari")["node"]["bytes"] == 0
    select(c, "Atari/2600")
    sync(c)
    n = tree(c, "Atari/2600")["node"]
    assert n["bytes"] == n["synced_bytes"] == sum(len(site.content(f)) for f in
                                                  ["Atari/2600/Adventure (USA).7z", "Atari/2600/Pitfall! (USA).7z",
                                                   "Atari/2600/Combat: Tank <Plus>.7z"])
    scan(c)                                                          # re-reading keeps them
    assert tree(c, "Atari/2600")["node"]["bytes"] == n["bytes"]


def test_ticked_folders_the_site_renamed_are_named(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari/2600")
    site.rename_dir("Atari/2600", "Atari/VCS")                       # e.g. Super Famicom -> SNES
    scan(c)
    log = _log(c)
    assert "Ticked folders that aren't on the site any more" in log and "Atari/2600" in log


def test_real_lolroms_pages_november_2026():
    """Trimmed copies of the live site's pages (scripts and styles removed), November 2026."""
    import os
    from lolroms_sync.core.extract import LinkExtractor
    from lolroms_sync.core.constants import DEFAULT_IGNORE_CLASSES, DOWNLOAD_EXTENSIONS
    here = os.path.join(os.path.dirname(__file__), "fixtures")
    root = open(os.path.join(here, "lolroms-root-2026-11.html"), "rb").read()
    folder = open(os.path.join(here, "lolroms-folder-2026-11.html"), "rb").read()

    def rules(path):
        cfg = Config(start_url="https://lolroms.com/" + path, keep_params=frozenset(),
                     ignore_classes=DEFAULT_IGNORE_CLASSES | {"path"}, download_exts=DOWNLOAD_EXTENSIONS)
        return cfg, PathRules(cfg, path)

    cfg, r = rules("")
    links, files, _ = LinkExtractor(cfg, r).extract(root, "https://lolroms.com/")
    assert {dir_of(u) for u in links} == {"Acorn", "ACT - Apricot PC Xi", "IBM - PC & Compatibles",
                                          "Nintendo", "Sinclair - ZX Spectrum +3", "Zeebo"}
    assert not files                                         # the header's bulk_download.py isn't one
    assert {dir_of(u) for u in header_dirs(root, "https://lolroms.com/", r)} >= \
        {"Arcade", "Pokéhacks", "FLAC", "DATs"}
    assert shown_dir(root) == ""

    cfg, r = rules("DATs/fixdats")
    assert shown_dir(folder) == "DATs/fixdats"
    links, files, _ = LinkExtractor(cfg, r).extract(folder, "https://lolroms.com/DATs/fixdats")
    assert links == []                                       # breadcrumb and ?sort= links skipped
    got = {r.rel_path(u): (i.date, i.size) for u, i in files.items()}
    assert got == {
        "DATs/fixdats/Arcade - Sega - Chihiro - Datfile (19) (2026-10-23 10-16-41).dat": ("2026-11-02", None),
        "DATs/fixdats/Atari - 8-bit Family (20260918-122830).dat": ("2026-09-18", None),
        "DATs/fixdats/Sinclair - ZX Spectrum +3 (20260516-213859).dat": ("2026-07-26", None),
    }
