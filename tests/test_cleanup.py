"""Clean up: files in the download folder that aren't part of the sync any more - listed after a
sync, and deleted only when confirmed."""
import os

from conftest import file_status, kids, on_disk, scan, select, sync


def leftovers(c):
    d = c.get("/api/cleanup").json()
    return {f["path"]: f["reason"] for f in d["files"]}


def delete(c, paths, confirm=True):
    return c.post("/api/cleanup/delete", json={"paths": paths, "confirm": confirm})


def test_lists_unticked_removed_and_unneeded_partial_files(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    assert leftovers(c) == {}
    select(c, "Atari/5200", False)                                  # no longer wanted
    site.files["Atari/2600"].remove("Pitfall! (USA).7z")            # removed from the site
    (c.data / "Atari/5200/Old.7z.part").write_bytes(b"x" * 10)     # a download never finished
    (c.data / "Atari/2600/Adventure (USA).7z.part").write_bytes(b"y")   # wanted: will be resumed
    st = sync(c)
    want = {"Atari/2600/Pitfall! (USA).7z": "gone", "Atari/5200/Galaxian (USA).7z": "unticked",
            "Atari/5200/Big.7z": "unticked", "Atari/5200/Old.7z.part": "part"}
    assert leftovers(c) == want
    assert st["leftovers"]["files"] == 4                             # shown after the sync
    assert any("aren't part of the sync" in line["msg"] for line in st["log"])


def test_nothing_is_deleted_without_confirmation(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    select(c, "Atari/5200", False)
    before = on_disk(c.data)
    assert delete(c, ["Atari/5200/Big.7z"], confirm=False).status_code == 400
    assert on_disk(c.data) == before


def test_deletes_only_confirmed_leftovers_and_empty_folders(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    select(c, "Atari/5200", False)
    r = delete(c, ["Atari/5200/Big.7z", "Atari/5200/Galaxian (USA).7z",
                   "Atari/2600/Adventure (USA).7z",                 # still wanted: kept
                   "../outside.7z"]).json()                         # never outside the folder
    assert sorted(r["deleted"]) == ["Atari/5200/Big.7z", "Atari/5200/Galaxian (USA).7z"]
    assert r["skipped"] == 2 and r["summary"]["files"] == 0
    assert (c.data / "Atari/2600/Adventure (USA).7z").exists()
    assert not (c.data / "Atari/5200").exists(), "a folder left empty is removed"
    assert (c.data / "Atari").is_dir() and (c.data / ".dir_crawler.db").exists()
    assert file_status(c, "Atari/5200")["Big.7z"] is None          # no longer shown as synced
    select(c, "Atari/5200", True)                                    # ticked again -> downloaded again
    site.reset_log()
    sync(c)
    assert (c.data / "Atari/5200/Big.7z").exists() and len(site.file_requests()) == 2


def test_folders_rom_sync_cant_judge_are_left_alone(site, app_factory):
    c = app_factory(site)
    scan(c, "Atari")                                                 # no full scan yet
    select(c, "Atari/2600")
    sync(c)
    (c.data / "Homebrew").mkdir()
    (c.data / "Homebrew/Mine.7z").write_bytes(b"1")                  # a folder the site never had
    (c.data / "ACT - Apricot PC Xi").mkdir()
    (c.data / "ACT - Apricot PC Xi/Disk.7z").write_bytes(b"2")       # a folder that fails to load
    (c.data / ".hidden").write_bytes(b"3")
    target = c.data / "Atari/2600"
    os.symlink(target, c.data / "link-to-2600")                      # symlinks are never followed
    assert leftovers(c) == {}
    scan(c)                                                          # a full scan: now it can tell
    assert kids(c)["ACT - Apricot PC Xi"]["error"]
    assert leftovers(c) == {"Homebrew/Mine.7z": "gone"}
    delete(c, ["Homebrew/Mine.7z", "link-to-2600"])
    assert not (c.data / "Homebrew").exists() and (c.data / "link-to-2600").is_symlink()
    assert (c.data / "ACT - Apricot PC Xi/Disk.7z").exists()


def test_cannot_delete_while_a_sync_runs(site_factory, app_factory):
    site = site_factory("slowfile")
    c = app_factory(site)
    scan(c)
    select(c, "Atari/5200")
    assert c.post("/api/sync").status_code == 200
    assert delete(c, ["Atari/5200/Big.7z"]).status_code == 409
    c.post("/api/stop")
