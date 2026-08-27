"""What changed: the files behind each Activity counter, and the change history by day."""
import time

from conftest import scan, select, sync

ADV = "Atari/2600/Adventure (USA).7z"


def files(c, kind):
    d = c.get("/api/sync/files", params={"kind": kind}).json()
    assert d["total"] == len(d["files"])
    return {f["path"]: f for f in d["files"]}


def test_each_counter_lists_its_files(site_factory, app_factory):
    site = site_factory("limitfile")
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    st = sync(c)
    new = files(c, "new")
    assert len(new) == st["sync"]["new"] == 7 and ADV in new and new[ADV]["size"] > 0
    failed = files(c, "failed")
    assert list(failed) == ["Atari/5200/Limited.7z"] and "limit" in failed["Atari/5200/Limited.7z"]["note"].lower()

    site.modify(ADV, "2026-01-05")
    sync(c)
    assert list(files(c, "updated")) == [ADV]                     # only this sync's changes
    assert files(c, "new") == {}
    assert len(files(c, "uptodate")) == 6 and ADV not in files(c, "uptodate")
    assert c.get("/api/sync/files", params={"kind": "nope"}).status_code == 400


def test_change_history_by_day_includes_clean_up(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    site.modify(ADV, "2026-01-05")
    sync(c)
    select(c, "Atari/5200", False)
    c.post("/api/cleanup/delete", json={"paths": ["Atari/5200/Big.7z"], "confirm": True})
    today = time.strftime("%Y-%m-%d")
    days = c.get("/api/changes/days").json()["days"]
    assert days[0]["day"] == today
    assert days[0]["counts"] == {"new": 7, "updated": 1, "deleted": 1}
    d = c.get("/api/changes", params={"day": today}).json()
    assert d["total"] == 9 and d["files"][0]["kind"] == "deleted"             # newest first
    assert d["files"][0]["note"] == "Not ticked any more"
    upd = c.get("/api/changes", params={"day": today, "kind": "updated"}).json()["files"]
    assert [f["path"] for f in upd] == [ADV]
