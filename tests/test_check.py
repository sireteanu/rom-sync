"""Check folder: re-reading the download folder corrects what counts as synced, without network
requests, downloads or deletes."""
import os

from conftest import file_status, kids, on_disk, scan, select, sync, wait
from mock_site import content

ADV = "Atari/2600/Adventure (USA).7z"
PIT = "Atari/2600/Pitfall! (USA).7z"
BIG = "Atari/5200/Big.7z"


def check(c):
    assert c.post("/api/check").status_code == 200
    return wait(c)


def synced_atari(site, c):
    scan(c)
    select(c, "Atari")
    sync(c)
    site.reset_log()


def test_files_deleted_by_hand_stop_counting_as_synced_and_come_back_at_next_sync(site, app_factory):
    c = app_factory(site)
    synced_atari(site, c)
    before = kids(c, "")["Atari"]["synced"]
    os.remove(c.data / ADV)
    st = check(c)
    assert st["job"]["kind"] == "check" and st["job"]["phase"] == "done"
    assert "1 synced files are missing" in st["job"]["message"]
    assert site.paths() == []                                    # no network at all
    assert file_status(c, "Atari/2600")["Adventure (USA).7z"] is None
    assert kids(c, "")["Atari"]["synced"] == before - 1
    deleted = c.get("/api/changes", params={"day": _today(), "kind": "deleted"}).json()["files"]
    assert [f["path"] for f in deleted] == [ADV]
    sync(c)
    assert len(site.file_requests()) == 1 and "Adventure" in site.file_requests()[0][0]
    assert (c.data / ADV).exists()


def test_files_copied_in_by_hand_count_as_synced(site, app_factory):
    c = app_factory(site)
    scan(c)                                                      # nothing ticked, nothing synced
    (c.data / "Atari/2600").mkdir(parents=True)
    (c.data / ADV).write_bytes(content(ADV))
    (c.data / PIT).write_bytes(b"7z\xbc\xaf\x27\x1c too short")   # doesn't fit the listing size
    st = check(c)
    assert "1 found that Rom Sync didn't know about" in st["job"]["message"]
    status = file_status(c, "Atari/2600")
    assert status["Adventure (USA).7z"] == "already in folder"
    assert status["Pitfall! (USA).7z"] is None
    assert kids(c, "Atari")["2600"]["synced"] == 1
    assert on_disk(c.data) == {ADV, PIT}                         # nothing deleted or moved


def test_a_file_cut_short_counts_as_not_synced_and_is_resumed(site, app_factory):
    c = app_factory(site)
    synced_atari(site, c)
    with open(c.data / BIG, "r+b") as f:
        f.truncate(1_000_000)
    st = check(c)
    assert "1 have a different size than expected" in st["job"]["message"]
    assert file_status(c, "Atari/5200")["Big.7z"] is None
    sync(c)
    assert [r for _, r in site.file_requests()] == ["bytes=1000000-"]
    assert file_status(c, "Atari/5200")["Big.7z"] == "resumed"


def test_nothing_changes_when_nothing_changed(site, app_factory):
    c = app_factory(site)
    synced_atari(site, c)
    before = {f: file_status(c, f) for f in ("Atari/2600", "Atari/5200")}
    st = check(c)
    assert "nothing changed" in st["job"]["message"]
    assert {f: file_status(c, f) for f in ("Atari/2600", "Atari/5200")} == before


def test_an_empty_or_missing_download_folder_changes_nothing(site, app_factory, tmp_path):
    """An unmounted share looks like every file was deleted: refuse instead."""
    c = app_factory(site)
    synced_atari(site, c)
    before = kids(c, "")["Atari"]["synced"]
    os.rename(c.data, tmp_path / "moved-away")
    st = check(c)
    assert "empty or can't be read" in st["job"]["message"]
    assert kids(c, "")["Atari"]["synced"] == before
    assert c.get("/api/changes", params={"day": _today(), "kind": "deleted"}).json()["files"] == []


def test_check_before_anything_was_downloaded(site, app_factory):
    c = app_factory(site)
    scan(c)
    st = check(c)
    assert st["job"]["phase"] == "done" and "nothing changed" in st["job"]["message"]
    assert not os.path.exists(c.data)                            # the check creates nothing


def test_check_refuses_while_busy(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    assert c.post("/api/sync").status_code == 200
    r = c.post("/api/check")
    wait(c)
    assert r.status_code in (200, 409)                           # 409 unless the sync already ended
    assert check(c)["job"]["kind"] == "check"


def test_compare_rules():
    from types import SimpleNamespace
    from lolroms_sync.folder_check import compare
    rec = SimpleNamespace(size=100)
    states = [
        ("u1", "a/ok.7z", "2025-01-01", "100 B", "new", 100),          # synced, unchanged
        ("u2", "a/gone.7z", "2025-01-01", "100 B", "up to date", 100), # synced, deleted by hand
        ("u3", "a/short.7z", "2025-01-01", "100 B", "new", 100),       # synced, now smaller
        ("u4", "a/hand.7z", "2025-01-01", "1.00 KB", None, None),      # copied in, fits listing
        ("u5", "a/never.7z", "2025-01-01", "1.00 KB", None, None),     # not there, never was
    ]
    disk = {"a/ok.7z": 100, "a/short.7z": 40, "a/hand.7z": 1024}
    res = compare(states, disk, lambda rel: rec if rel in ("a/ok.7z", "a/gone.7z", "a/short.7z") else None)
    assert res["found"] == [("u4", "already in folder", 1024)]
    assert sorted(res["lost"]) == ["u2", "u3"]
    assert res["missing"] == ["a/gone.7z"] and res["changed"] == ["a/short.7z"]
    assert res["counts"] == {"checked": 5, "present": 2, "added": 1, "missing": 1, "changed": 1}


def _today():
    import time
    return time.strftime("%Y-%m-%d")
