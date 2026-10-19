"""The sync engine through the web app: what gets downloaded, resumed, updated, skipped, dated,
and how many downloads run at once."""
import os
import shutil
import threading
import time

from conftest import file_status, on_disk, scan, select, sync, wait
from mock_site import content

from lolroms_sync.core.sync import Manifest

ADV = "Atari/2600/Adventure (USA).7z"
BIG = "Atari/5200/Big.7z"
EXPECTED = {
    "Atari/2600/Adventure (USA).7z",
    "Atari/2600/Pitfall! (USA).7z",
    "Atari/2600/Combat_ Tank _Plus_.7z",      # ':' '<' '>' made Windows-safe
    "Atari/5200/Galaxian (USA).7z",
    "Atari/5200/Big.7z",
    "Atari/800/Flux/Flux A.zip",
    "Atari/800/Flux/_CON.7z",                 # reserved Windows name
}


def source_name(rel):
    return {"Atari/2600/Combat_ Tank _Plus_.7z": "Atari/2600/Combat: Tank <Plus>.7z",
            "Atari/800/Flux/_CON.7z": "Atari/800/Flux/CON.7z"}.get(rel, rel)


def atari(c):
    """Scan the site, tick Atari, sync it."""
    scan(c)
    select(c, "Atari")
    return sync(c)


def first(site, c):
    atari(c)
    site.reset_log()


def fetched(site):
    return sorted({p.split("file=")[1] for p, _ in site.file_requests()})


def local_day(path):
    return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))


def all_status(c):
    out = {}
    for folder in ("Atari/2600", "Atari/5200", "Atari/800/Flux", "Atari/Bulk"):
        for name, word in file_status(c, folder).items():
            if not name.endswith((".txt", ".iso")):          # not synced with the default types
                out[f"{folder}/{name}"] = word
    return out


# ------------------------------------------------------------------ downloading
def test_downloads_archives_into_mirrored_folders(site, app_factory):
    c = app_factory(site)
    st = atari(c)
    assert on_disk(c.data) == EXPECTED
    for rel in EXPECTED:
        assert (c.data / rel).read_bytes() == content(source_name(rel)), rel
    assert st["sync"]["new"] == 7
    big = next(f for f in c.get("/api/files", params={"path": "Atari/5200"}).json()["files"]
               if f["name"] == "Big.7z")
    assert big["date"] == "2025-11-17" and big["local"] == "new"
    assert not any("file=Acorn" in p or "file=Arcade" in p for p in site.paths())


def test_rejects_web_page_served_instead_of_file(site_factory, app_factory):
    site = site_factory("limitfile")
    c = app_factory(site)
    atari(c)
    assert not (c.data / "Atari/5200/Limited.7z").exists()
    assert not (c.data / "Atari/5200/Limited.7z.part").exists()
    lim = next(f for f in c.get("/api/files", params={"path": "Atari/5200"}).json()["files"]
               if f["name"] == "Limited.7z")
    assert "limit" in (lim["error"] or "").lower()
    assert on_disk(c.data) == EXPECTED, "other files still download"


def test_resumes_dropped_transfer_with_range(site_factory, app_factory):
    site = site_factory("drop")
    c = app_factory(site)
    atari(c)
    assert (c.data / BIG).read_bytes() == content(BIG)
    ranges = [r for p, r in site.file_requests() if "Big.7z" in p]
    assert ranges[0] is None and all(r and r.startswith("bytes=") for r in ranges[1:]), ranges
    assert len(ranges) == 3


def test_a_new_catalog_skips_files_already_in_the_folder(site_factory, app_factory):
    a = site_factory()
    atari(app_factory(a))
    b = site_factory()
    c2 = app_factory(b, name="config2")                 # e.g. the catalog was reset
    atari(c2)
    assert b.file_requests() == [], "nothing changed, so nothing is downloaded"
    assert set(all_status(c2).values()) == {"up to date"}


def test_stop_keeps_part_file_and_next_sync_finishes_it(site_factory, app_factory):
    site = site_factory("slowfile")
    c = app_factory(site)
    scan(c)
    select(c, "Atari/5200")
    assert c.post("/api/sync").status_code == 200
    part = c.data / (BIG + ".part")
    deadline = time.time() + 20
    while time.time() < deadline and not (part.exists() and part.stat().st_size > 500_000):
        time.sleep(0.1)
    assert part.exists(), "download never started"
    c.post("/api/stop")
    wait(c)
    partial = part.stat().st_size
    assert 0 < partial < len(content(BIG))
    sync(c)
    assert (c.data / BIG).read_bytes() == content(BIG)
    resumed = [rng for path, rng in site.file_requests() if "Big.7z" in path][-1]
    assert resumed == f"bytes={partial}-"


# ------------------------------------------------------------------ deciding what to fetch
def test_changed_file_on_site_is_updated(site, app_factory):
    c = app_factory(site)
    first(site, c)
    site.modify(ADV, "2026-01-05")                       # new date, same size
    sync(c)                                              # re-reads the ticked listings first
    assert fetched(site) == ["Atari%2F2600%2FAdventure+%28USA%29.7z"]
    assert (c.data / ADV).read_bytes() == site.content(ADV)
    st = all_status(c)
    assert st[ADV] == "updated"
    assert sum(1 for v in st.values() if v == "up to date") == 6


def test_size_change_with_same_date_is_updated(site, app_factory):
    c = app_factory(site)
    first(site, c)
    site.modify(ADV, "2025-11-17", grow=5000)            # same day, different size
    sync(c)
    assert (c.data / ADV).read_bytes() == site.content(ADV)
    assert all_status(c)[ADV] == "updated"


def test_truncated_file_is_resumed_not_restarted(site, app_factory):
    c = app_factory(site)
    first(site, c)
    with open(c.data / BIG, "r+b") as f:
        f.truncate(1_000_000)
    sync(c)
    assert (c.data / BIG).read_bytes() == site.content(BIG)
    assert [r for p, r in site.file_requests()] == ["bytes=1000000-"]
    assert all_status(c)[BIG] == "resumed"


def test_deleted_file_is_downloaded_again(site, app_factory):
    c = app_factory(site)
    first(site, c)
    os.remove(c.data / ADV)
    sync(c)
    assert (c.data / ADV).exists() and len(site.file_requests()) == 1
    assert all_status(c)[ADV] == "new"


def test_existing_files_from_elsewhere_are_adopted(site, app_factory):
    c = app_factory(site)
    first(site, c)
    for extra in ("", "-wal", "-shm"):                    # e.g. copied from another PC
        p = str(c.data / ".dir_crawler.db") + extra
        if os.path.exists(p):
            os.remove(p)
    with open(c.data / BIG, "r+b") as f:                   # one of them is incomplete
        f.truncate(2_000_000)
    sync(c)
    st = all_status(c)
    assert st[BIG] == "resumed"
    assert sum(1 for v in st.values() if v == "already in folder") == 6
    assert [r for p, r in site.file_requests()] == ["bytes=2000000-"]
    site.reset_log()
    sync(c)
    assert site.file_requests() == []
    assert set(all_status(c).values()) == {"up to date"}


def test_part_file_restarts_if_file_changed_meanwhile(site, app_factory):
    c = app_factory(site)
    first(site, c)
    old_etag = site.etag(BIG)
    shutil.move(c.data / BIG, str(c.data / BIG) + ".part")      # pretend it was interrupted...
    with open(str(c.data / BIG) + ".part", "r+b") as f:
        f.truncate(1_000_000)
    m = Manifest(str(c.data))
    m.part_started(BIG, "", old_etag)
    m.close()
    site.modify(BIG, "2026-02-01")                               # ...and the site changed it since
    sync(c)
    hdrs = [h for p, h in site.file_headers() if "Big.7z" in p]
    assert hdrs[0].get("If-Range") == old_etag
    assert (c.data / BIG).read_bytes() == site.content(BIG), "must restart, not append to the old version"


def test_sites_without_listing_dates_use_conditional_requests(site_factory, app_factory):
    site = site_factory("nometa")
    c = app_factory(site)
    first(site, c)
    sync(c)
    hdrs = [h for _, h in site.file_headers()]
    assert len(hdrs) == 7 and all("If-None-Match" in h or "If-Modified-Since" in h for h in hdrs)
    assert set(all_status(c).values()) == {"up to date"}
    site.reset_log()
    site.modify(ADV, "2026-03-01")
    sync(c)
    assert (c.data / ADV).read_bytes() == site.content(ADV)
    assert all_status(c)[ADV] == "updated"


def test_check_remote_asks_server_about_every_file(site, app_factory):
    c = app_factory(site)
    first(site, c)
    assert c.post("/api/settings", json={"check_remote": True}).status_code == 200
    sync(c)
    assert len(site.file_requests()) == 7
    assert set(all_status(c).values()) == {"up to date"}


# ------------------------------------------------------------------ file dates
def test_file_date_is_the_websites_date_at_noon(site, app_factory):
    site.lm_shift_days = 40                     # server says a different day than the listing
    c = app_factory(site)
    atari(c)
    assert local_day(c.data / ADV) == "2025-11-17"
    assert time.localtime(os.path.getmtime(c.data / ADV)).tm_hour == 12


def test_updated_file_gets_the_new_website_date(site, app_factory):
    c = app_factory(site)
    first(site, c)
    site.modify(ADV, "2026-03-14")
    sync(c)
    assert local_day(c.data / ADV) == "2026-03-14"
    assert local_day(c.data / BIG) == "2025-11-17"   # untouched files keep theirs


def test_dates_of_files_already_in_folder_are_corrected_without_downloading(site, app_factory):
    c = app_factory(site)
    first(site, c)
    for rel in (ADV, BIG):
        os.utime(c.data / rel, (1_000_000_000, 1_000_000_000))   # 2001 - e.g. copied from elsewhere
    sync(c)
    assert site.file_requests() == []
    assert local_day(c.data / ADV) == "2025-11-17" and local_day(c.data / BIG) == "2025-11-17"


def test_files_saved_without_dates_get_dated_on_next_sync(site_factory, app_factory):
    """The real-world case: early versions couldn't read lolroms' dates, so files kept their
    download time."""
    plain = site_factory("nometa")
    c = app_factory(plain)
    atari(c)
    for rel in (ADV, BIG):
        os.utime(c.data / rel, (1_900_000_000, 1_900_000_000))     # "download time"
    dated = site_factory()
    c2 = app_factory(dated, name="config2")
    atari(c2)
    assert dated.file_requests() == []
    assert local_day(c2.data / ADV) == "2025-11-17" and local_day(c2.data / BIG) == "2025-11-17"
    assert set(all_status(c2).values()) == {"up to date"}


# ------------------------------------------------------------------ parallel downloads
def test_finds_and_remembers_the_servers_limit(site_factory, app_factory):
    site = site_factory("bulk")
    site.max_conc = 4
    c = app_factory(site, settings={"parallel": "auto"})
    atari(c)
    assert set(all_status(c).values()) == {"new"}
    assert site.peak_active == 4, "should run as many downloads as the server allows"
    m = Manifest(str(c.data))
    assert m.get_meta("max_parallel:127.0.0.1") == "4"
    m.close()

    # a later sync starts straight at the remembered limit and never exceeds it
    again = site_factory("bulk")
    again.max_conc = 4
    shutil.rmtree(c.data / "Atari" / "Bulk")
    c2 = app_factory(again, name="config2", settings={"parallel": "auto"})
    atari(c2)
    assert again.rejected == 0 and again.peak_active == 4


def test_rechecks_and_rises_when_the_server_allows_more(site_factory, app_factory):
    site = site_factory("bulk")
    site.max_conc = 2
    c = app_factory(site, tuning={"ramp": 0.2, "confirm": 0.5, "probe": 1}, settings={"parallel": "auto"})
    scan(c)
    select(c, "Atari")
    timer = threading.Timer(2.0, lambda: setattr(site, "max_conc", 6))
    timer.start()
    try:
        sync(c)
    finally:
        timer.cancel()
    assert site.peak_active >= 5, f"peak {site.peak_active}: should have re-probed after the limit went up"
    assert set(all_status(c).values()) == {"new"}


def test_fixed_number_of_parallel_downloads(site_factory, app_factory):
    site = site_factory("bulk")
    c = app_factory(site, settings={"parallel": "3"})
    atari(c)
    assert site.peak_active == 3


def test_daily_limit_pauses_and_the_next_sync_finishes(site, app_factory):
    site.daily_limit = 3
    c = app_factory(site)
    st = atari(c)
    assert st["sync"]["new"] >= 3 and len(on_disk(c.data)) < 7
    site.daily_limit = None                                  # "next day"
    sync(c)
    assert on_disk(c.data) == EXPECTED


# ------------------------------------------------------------------ reading listings
def listed(c, folder="Atari/5200"):
    return {f["name"]: f for f in c.get("/api/files", params={"path": folder}).json()["files"]}


def test_reads_dates_and_sizes_from_lolroms_rows(site, app_factory):
    c = app_factory(site)
    scan(c, "Atari")
    big = listed(c)["Big.7z"]
    assert big["date"] == "2025-11-17" and big["size"] == "2.86 MB"
    assert all(f["date"] and f["size"] for f in listed(c, "Atari/2600").values())


def test_reads_dates_and_sizes_from_table_layouts_too(site_factory, app_factory):
    c = app_factory(site_factory("tables"))
    scan(c, "Atari")
    rows = listed(c, "Atari/2600").values()
    assert rows and all(f["date"] == "2025-11-17" and f["size"] for f in rows)


def test_folder_scan_stays_in_scope_and_never_fetches_files(site, app_factory):
    c = app_factory(site)
    scan(c, "Atari")
    paths = site.paths()
    assert not site.file_requests(), "file links are never requested while reading listings"
    assert not [p for p in paths if "dir=" in p and "Atari" not in p], "must not leave Atari"
    pages = [p for p in paths if "dir=" in p]
    assert len(pages) == len(set(pages)) and not [p for p in paths if "sort=" in p]


def test_scan_survives_rate_limiting(site_factory, app_factory):
    site = site_factory("throttle")
    c = app_factory(site)
    scan(c, "Atari")
    assert set(listed(c, "Atari/2600")) == {"Adventure (USA).7z", "Pitfall! (USA).7z", "Combat_ Tank _Plus_.7z"}
    assert listed(c, "Atari/800/Flux")


def test_a_sync_with_nothing_to_do_writes_nothing_to_the_disk(site, app_factory, monkeypatch):
    """Files that are up to date, correctly dated and already have the permissions setting are
    only looked at (one lookup each) - no permission or date changes, which are slow on shares."""
    c = app_factory(site, settings={"file_mode": "777"})
    atari(c)
    writes = []
    real_chmod, real_utime = os.chmod, os.utime
    monkeypatch.setattr(os, "chmod", lambda *a, **k: (writes.append(("chmod", a[0])), real_chmod(*a, **k))[1])
    monkeypatch.setattr(os, "utime", lambda *a, **k: (writes.append(("utime", a[0])), real_utime(*a, **k))[1])
    st = sync(c)
    assert st["sync"]["up to date"] == 7
    assert [w for w in writes if str(w[1]).endswith((".7z", ".zip"))] == []



def test_folder_map_saves_site_folders_elsewhere(site_factory, app_factory):
    """Folder map: a site folder (matched with *) is saved under another folder name; the tree
    shows where; Clean up doesn't flag those files; removing the rule moves them back."""
    from conftest import kids, on_disk, scan, select, sync
    site = site_factory("dated")
    c = app_factory(site)
    scan(c)
    dated = "Acorn/Archimedes (20231029-220453)"
    select(c, dated)
    r = c.post("/api/folder-map", json={"rules": [{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}]})
    assert r.status_code == 200, r.text
    assert c.get("/api/folder-map").json()["rules"] == [{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}]
    assert kids(c, "Acorn")["Archimedes (20231029-220453)"]["saved_to"] == "Acorn/Archimedes"
    assert kids(c)["Acorn"]["saved_to"] is None
    sync(c)
    assert on_disk(c.data) == {"Acorn/Archimedes/Zarch.7z"}
    assert c.get("/api/cleanup").json()["files"] == []
    n = len(site.file_requests())
    sync(c)                                                   # nothing new to fetch
    assert len(site.file_requests()) == n
    # without the rule the file belongs in the site's folder again; the old copy is a leftover
    assert c.post("/api/folder-map", json={"rules": []}).status_code == 200
    sync(c)
    assert on_disk(c.data) == {"Acorn/Archimedes/Zarch.7z", f"{dated}/Zarch.7z"}
    left = {f["path"]: f["reason"] for f in c.get("/api/cleanup").json()["files"]}
    assert left == {"Acorn/Archimedes/Zarch.7z": "gone"}


def test_folder_map_bad_rules_are_refused(site, app_factory):
    c = app_factory(site)
    r = c.post("/api/folder-map", json={"rules": [{"site": "Acorn", "local": "../x"}]})
    assert r.status_code == 400 and "can't be" in r.json()["error"]
    # the generic settings endpoint can't change it behind the dialog's back
    c.post("/api/settings", json={"folder_map": [{"site": "x", "local": "y"}]})
    assert c.get("/api/folder-map").json()["rules"] == []


def test_paths_from_1_3_7_are_put_back_at_start(site_factory, app_factory, tmp_path):
    """1.3.7/1.3.8 saved folders without their date stamp; without a folder map rule the catalog
    points at the site's folder names again after the update."""
    import sqlite3
    from conftest import scan
    site = site_factory("dated")
    c = app_factory(site)
    scan(c)
    c.__exit__(None, None, None)
    db = sqlite3.connect(tmp_path / "config" / "catalog.db")
    db.execute("UPDATE files SET rel='Acorn/Archimedes/Zarch.7z' WHERE name='Zarch.7z'")
    db.commit()
    db.close()
    app_factory(site)
    db = sqlite3.connect(tmp_path / "config" / "catalog.db")
    assert db.execute("SELECT rel FROM files WHERE name='Zarch.7z'").fetchone()[0] == \
        "Acorn/Archimedes (20231029-220453)/Zarch.7z"


OLD_SET = "Archimedes (20231029-220453)"
NEW_SET = "Archimedes (20261001-120000)"


def rename_on_site(site, old=OLD_SET, new=NEW_SET):
    """lolroms moves a folder to a newer set date: same files, new folder name."""
    site.tree["Acorn"] = [new if n == old else n for n in site.tree["Acorn"]]
    site.tree[f"Acorn/{new}"] = site.tree.pop(f"Acorn/{old}")
    site.files[f"Acorn/{new}"] = site.files.pop(f"Acorn/{old}")


def test_ticks_follow_a_folder_renamed_to_a_newer_set_date_in_a_scan(site_factory, app_factory):
    from conftest import kids, on_disk, scan, select, sync
    site = site_factory("dated")
    c = app_factory(site)
    scan(c)
    select(c, f"Acorn/{OLD_SET}")
    c.post("/api/folder-map", json={"rules": [{"site": "Acorn/Archimedes (*)", "local": "Acorn/Archimedes"}]})
    sync(c)
    rename_on_site(site)
    st = scan(c)
    assert any("renamed" in l["msg"] for l in c.get("/api/status", params={"log_since": 0}).json()["log"])
    k = kids(c, "Acorn")
    assert OLD_SET not in k and k[NEW_SET]["state"] == "on" and k[NEW_SET]["saved_to"] == "Acorn/Archimedes"
    n = len(site.file_requests())
    sync(c)                                    # same files, same folder on disk: nothing to fetch
    assert len(site.file_requests()) == n
    assert on_disk(c.data) == {"Acorn/Archimedes/Zarch.7z"}
    assert c.get("/api/cleanup").json()["files"] == []
    assert st["busy"] is False


def test_a_sync_finds_a_renamed_ticked_folder_by_itself(site_factory, app_factory):
    """No scan in between: the sync can't read the old folder, re-reads its parent, finds the
    renamed folder and carries on; an exact Folder map rule follows the folder too."""
    from conftest import kids, on_disk, scan, select, sync
    site = site_factory("dated")
    c = app_factory(site)
    scan(c)
    select(c, f"Acorn/{OLD_SET}")
    c.post("/api/folder-map", json={"rules": [{"site": f"Acorn/{OLD_SET}", "local": "Acorn/Archimedes"}]})
    sync(c)
    rename_on_site(site)
    site.files[f"Acorn/{NEW_SET}"].append("Lander.7z")         # the new set has one more game
    n = len(site.file_requests())
    sync(c)
    assert len(site.file_requests()) == n + 1                  # only the new game
    assert on_disk(c.data) == {"Acorn/Archimedes/Zarch.7z", "Acorn/Archimedes/Lander.7z"}
    assert kids(c, "Acorn")[NEW_SET]["state"] == "on"
    assert c.get("/api/folder-map").json()["rules"] == [{"site": f"Acorn/{NEW_SET}", "local": "Acorn/Archimedes"}]


def test_follow_renames_only_moves_ticks_to_a_folder_with_the_same_name(tmp_path):
    from lolroms_sync.catalog import Catalog
    cat = Catalog(str(tmp_path / "catalog.db"))
    cat.add_dirs({"Acorn", "Acorn/Archimedes (20261001-120000)", "Acorn/Atom (20261001-120000)"}, 1)
    cat.db.executemany("INSERT INTO selection VALUES (?,?)", [
        ("Acorn/Archimedes (20231029-220453)", 1), ("Acorn/Archimedes (20231029-220453)/.", 1),
        ("Acorn/Risc PC (20231029-220453)", 1), ("Acorn/Gone", 1)])
    renames = cat.follow_renames()
    assert renames == {"Acorn/Archimedes (20231029-220453)": "Acorn/Archimedes (20261001-120000)"}
    assert cat.rules() == {"Acorn/Archimedes (20261001-120000)": True, "Acorn/Archimedes (20261001-120000)/.": True,
                           "Acorn/Risc PC (20231029-220453)": True, "Acorn/Gone": True}


def test_saved_as_names_a_folder_on_disk(site_factory, app_factory):
    """The tree's "Saved as" column: a folder gets another name on disk, the tree shows it, the
    name follows the folder when the site moves it to a newer set date, empty = the site's name."""
    from conftest import kids, on_disk, scan, select, sync
    site = site_factory("dated")
    c = app_factory(site)
    scan(c)
    select(c, f"Acorn/{OLD_SET}")
    node = kids(c, "Acorn")[OLD_SET]
    assert (node["disk_name"], node["name_set"], node["saved_to"]) == (OLD_SET, False, None)
    r = c.post("/api/folder-name", json={"path": f"Acorn/{OLD_SET}", "name": " Archimedes "})
    assert r.status_code == 200, r.text
    node = kids(c, "Acorn")[OLD_SET]
    assert (node["disk_name"], node["name_set"], node["saved_to"]) == ("Archimedes", True, None)
    sync(c)
    assert on_disk(c.data) == {"Acorn/Archimedes/Zarch.7z"}
    rename_on_site(site)
    n = len(site.file_requests())
    sync(c)                                                    # the name went along: nothing to fetch
    assert len(site.file_requests()) == n
    assert kids(c, "Acorn")[NEW_SET]["disk_name"] == "Archimedes"
    assert c.post("/api/folder-name", json={"path": f"Acorn/{NEW_SET}", "name": ""}).status_code == 200
    assert kids(c, "Acorn")[NEW_SET]["disk_name"] == NEW_SET
    assert c.post("/api/folder-name", json={"path": "Acorn", "name": "a/b"}).status_code == 400


def test_follow_renames_knows_both_set_date_forms(tmp_path):
    """lolroms writes set dates as (20231029-220453) and as (2026-10-14 05-51-53); a folder can
    move from one form to the other, and the newest date wins whatever its form."""
    from lolroms_sync.catalog import Catalog, set_stem
    assert set_stem("PC Engine CD & TurboGrafx CD (2026-10-14 05-51-53)") == "pc engine cd & turbografx cd"
    cat = Catalog(str(tmp_path / "catalog.db"))
    cat.add_dirs({"NEC", "NEC/PC Engine CD (2026-10-14 05-51-53)", "NEC/PC Engine CD (20251001-000000)"}, 1)
    cat.db.execute("INSERT INTO selection VALUES ('NEC/PC Engine CD (20240101-000000)', 1)")
    assert cat.follow_renames() == {"NEC/PC Engine CD (20240101-000000)": "NEC/PC Engine CD (2026-10-14 05-51-53)"}
