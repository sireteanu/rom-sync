"""The site's list of recent changes (rss.xml): a sync re-reads only the ticked folders it names,
and falls back to re-reading everything whenever the list can't vouch for a folder."""
import json
import sqlite3
import time

from conftest import on_disk, scan, select, sync

ATARI = ("Atari/2600", "Atari/5200")


def dir_reads(site):
    return sorted(p for p in site.paths() if "dir=" in p)


def feed_reads(site):
    return [p for p in site.paths() if p.startswith("/rss.xml")]


def setup(site_factory, app_factory, modes="feed", settings=None):
    site = site_factory(modes)
    c = app_factory(site, settings=settings)
    scan(c)                                    # a full scan: everything known as of now
    for p in ATARI:
        select(c, p)
    sync(c)
    site.reset_log()
    return site, c


def test_nothing_changed_means_no_folder_is_read(site_factory, app_factory):
    site, c = setup(site_factory, app_factory)
    sync(c)
    assert feed_reads(site) and dir_reads(site) == [] and site.file_requests() == []
    log = " ".join(l["msg"] for l in c.get("/api/status", params={"log_since": 0}).json()["log"])
    assert "nothing new in 2 of your ticked folders" in log


def test_only_changed_folders_are_read_and_their_files_fetched(site_factory, app_factory):
    site, c = setup(site_factory, app_factory)
    site.modify("Atari/5200/Galaxian (USA).7z", "2026-10-30", grow=100)
    site.add_file("Atari/2600", "Brand New (USA).7z")
    site.add_file("Atari/Lynx", "Not Ticked.7z")              # in the feed, but not ticked
    sync(c)
    assert dir_reads(site) == ["/?dir=Atari%2F2600", "/?dir=Atari%2F5200"]
    fetched = sorted(p for p, _ in site.file_requests())
    assert len(fetched) == 2 and any("Brand+New" in p for p in fetched) and any("Galaxian" in p for p in fetched)
    assert "Atari/2600/Brand New (USA).7z" in on_disk(c.data)
    site.reset_log()
    sync(c)                                  # known now (they may be read once more within minutes)
    assert site.file_requests() == []


def test_a_list_too_short_to_reach_back_means_a_full_re_read(site_factory, app_factory):
    site, c = setup(site_factory, app_factory)
    site.feed_cap = 1
    site.add_file("Atari/Lynx", "One.7z")
    time.sleep(0.01)
    site.add_file("Atari/Lynx", "Two.7z")                     # the list now starts after the last sync
    sync(c)
    assert dir_reads(site) == ["/?dir=Atari%2F2600", "/?dir=Atari%2F5200"]


def test_a_newly_ticked_folder_is_trusted_from_the_last_full_scan(site_factory, app_factory):
    site, c = setup(site_factory, app_factory)
    select(c, "Atari/800")
    site.add_file("Atari/800/Flux", "Fresh.zip")
    sync(c)
    assert dir_reads(site) == ["/?dir=Atari%2F800%2FFlux"]
    assert "Atari/800/Flux/Fresh.zip" in on_disk(c.data)


def test_every_folder_is_still_re_read_in_full_once_a_day(site_factory, app_factory, tmp_path):
    site, c = setup(site_factory, app_factory)
    db = sqlite3.connect(tmp_path / "config" / "catalog.db")
    (raw,) = db.execute("SELECT v FROM settings WHERE k='feed_state'").fetchone()
    st = json.loads(raw)
    old = time.time() - 2 * 86400
    st["site_read"] = old
    st["roots"] = {r: {"read": old, "known": time.time()} for r in st.get("roots", {})}
    db.execute("UPDATE settings SET v=? WHERE k='feed_state'", (json.dumps(st),))
    db.commit()
    db.close()
    sync(c)
    assert dir_reads(site) == ["/?dir=Atari%2F2600", "/?dir=Atari%2F5200"]


def test_without_a_list_or_when_turned_off_everything_is_read(site_factory, app_factory):
    site, c = setup(site_factory, app_factory, modes="")       # no rss.xml on this site
    sync(c)
    assert dir_reads(site) == ["/?dir=Atari%2F2600", "/?dir=Atari%2F5200"]
    site, c = setup(site_factory, app_factory, settings={"use_feed": False})
    sync(c)
    assert feed_reads(site) == [] and dir_reads(site) == ["/?dir=Atari%2F2600", "/?dir=Atari%2F5200"]


def test_parse_feed_and_plan():
    from lolroms_sync import feed
    body = (b'<?xml version="1.0"?><rss><channel><lastBuildDate>Fri, 30 Oct 2026 18:48:56 GMT</lastBuildDate>'
            b'<item><link>https://lolroms.com/?file=Nintendo%2FNintendo+64%2FHacks+%5bX%5d%2FHalo.7z</link>'
            b'<pubDate>Fri, 30 Oct 2026 11:57:27 GMT</pubDate></item>'
            b'<item><link>https://lolroms.com/?file=Commodore%2F64%2FBis.7z</link>'
            b'<pubDate>Thu, 29 Oct 2026 22:47:19 GMT</pubDate></item></channel></rss>')
    items, built = feed.parse_feed(body)
    assert [d for d, _ in items] == ["Nintendo/Nintendo 64/Hacks [X]", "Commodore/64"]
    oldest = min(t for _, t in items)
    now = built + 60
    # known just after the oldest change: the list holds everything since, Commodore's change is known
    st = {"site_read": oldest + 60}
    p = feed.plan(["Nintendo/Nintendo 64", "Commodore/64", "SEGA"], st, items, built, now)
    assert p["covered"] == ["Nintendo/Nintendo 64", "Commodore/64", "SEGA"] and p["full"] == []
    assert p["folders"] == ["Nintendo/Nintendo 64/Hacks [X]"]
    # a root known even later (read after Halo changed) needs nothing
    st["roots"] = {"Nintendo": {"read": now - 600, "known": now - 600}}
    assert feed.plan(["Nintendo/Nintendo 64"], st, items, built, now)["folders"] == []
    p = feed.plan(["SEGA"], {"site_read": oldest - 60}, items, built, now)      # list too short
    assert p["full"] == ["SEGA"] and p["folders"] == []
    p = feed.plan(["SEGA"], {}, items, built, now)                               # never read
    assert p["full"] == ["SEGA"]
    for bad in (b"<html>not a feed", b"<rss><channel></channel></rss>"):
        try:
            feed.parse_feed(bad)
            raise AssertionError("should not parse")
        except ValueError:
            pass
