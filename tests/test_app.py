"""The web app (lolroms_sync): scan, selection, sync, settings, auth - through its HTTP API."""
import base64
import os
import time

import pytest
from fastapi.testclient import TestClient

from lolroms_sync.engine import Engine
from lolroms_sync.web import create_app

TUNING = {"ramp": 0.2, "confirm": 0.5, "probe": 300}


@pytest.fixture
def app_factory(tmp_path):
    clients = []

    def make(site, password=None, data_locked=False):
        cfg, data = tmp_path / "config", tmp_path / "data"
        app = create_app(lambda: Engine(str(cfg), str(data), site.url + "/", data_locked=data_locked,
                                        scan_delay=0, tuning=TUNING), password=password)
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        c.data = data
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def wait(c, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get("/api/status").json()
        if not st["busy"]:
            return st
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def scan(c, root=""):
    assert c.post("/api/scan", json={"root": root}).status_code == 200
    return wait(c)


def tree(c, path=""):
    return c.get("/api/tree", params={"path": path}).json()


def kids(c, path=""):
    return {k["name"]: k for k in tree(c, path)["children"]}


def select(c, path, include=True):
    assert c.post("/api/select", json={"path": path, "include": include}).status_code == 200


def sync(c):
    assert c.post("/api/sync").status_code == 200
    return wait(c)


def on_disk(folder):
    out = set()
    for root, _, names in os.walk(folder):
        for n in names:
            if not n.startswith(".dir_crawler.db"):
                out.add(os.path.relpath(os.path.join(root, n), folder).replace(os.sep, "/"))
    return out


def test_full_scan_builds_the_catalog_without_touching_files(site, app_factory):
    c = app_factory(site)
    st = scan(c)
    assert not site.file_requests()
    top = kids(c)
    assert set(top) == {"Acorn", "ACT - Apricot PC Xi", "Arcade", "Atari"}
    assert top["ACT - Apricot PC Xi"]["error"] == "HTTP 404"          # a broken folder is shown, not fatal
    atari = tree(c, "Atari")["node"]
    assert atari["files"] == 7                                       # archives only (no .iso / .txt)
    assert set(kids(c, "Atari")) == {"2600", "5200", "800", "Lynx"}
    assert kids(c, "Atari/800")["Flux"]["files"] == 2
    assert st["catalog"]["files"] == 9 and st["catalog"]["last_full_scan"]["status"] == "done"
    pages = [p for p in site.paths() if "dir=" in p]
    assert len(pages) == len(set(pages)), "each listing is read once"


def test_tick_states_and_inheritance(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    select(c, "Atari/5200", False)
    assert tree(c, "Atari")["node"]["state"] == "partial"
    assert kids(c, "Atari")["2600"]["state"] == "on"
    assert kids(c, "Atari")["5200"]["state"] == "off"
    assert kids(c)["Atari"]["state"] == "partial" and kids(c)["Acorn"]["state"] == "off"
    select(c, "Atari/5200", True)                                    # back to a plain tick
    assert tree(c, "Atari")["node"]["state"] == "on"
    sel = c.get("/api/status").json()["selection"]
    assert sel["files"] == 7
    assert c.post("/api/selection/clear").status_code == 200
    assert c.get("/api/status").json()["selection"]["files"] == 0


def test_sync_downloads_only_ticked_folders_then_nothing(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    select(c, "Atari/5200", False)
    st = sync(c)
    assert on_disk(c.data) == {"Atari/2600/Adventure (USA).7z", "Atari/2600/Pitfall! (USA).7z",
                               "Atari/2600/Combat_ Tank _Plus_.7z", "Atari/800/Flux/Flux A.zip",
                               "Atari/800/Flux/_CON.7z"}
    assert st["sync"]["new"] == 5 and st["sync"]["failed"] == 0
    assert tree(c, "Atari")["node"]["synced"] == 5
    assert kids(c, "Atari")["2600"]["synced"] == 3
    site.reset_log()
    st = sync(c)
    assert site.file_requests() == []
    assert st["sync"]["up to date"] == 5


def test_new_folders_on_the_site_inherit_the_tick(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    site.tree["Atari"].append("Jaguar")
    site.tree["Atari/Jaguar"] = []
    site.files["Atari/Jaguar"] = ["Tempest 2000 (World).7z"]
    sync(c)                                    # refreshes the ticked folders' listings first
    assert "Atari/Jaguar/Tempest 2000 (World).7z" in on_disk(c.data)
    assert kids(c, "Atari")["Jaguar"]["state"] == "on"


def test_rescan_forgets_files_removed_from_the_site(site, app_factory):
    c = app_factory(site)
    scan(c)
    assert kids(c, "Atari")["2600"]["files"] == 3
    site.files["Atari/2600"].remove("Pitfall! (USA).7z")
    scan(c, "Atari")
    assert kids(c, "Atari")["2600"]["files"] == 2


def test_folder_files_listing(site, app_factory):
    c = app_factory(site)
    scan(c)
    data = c.get("/api/files", params={"path": "Atari/2600"}).json()
    assert data["total"] == 3
    assert {f["date"] for f in data["files"]} == {"2025-11-17"}
    assert all(f["size"] for f in data["files"])


def test_settings_validate_and_persist(site, app_factory):
    c = app_factory(site)
    r = c.post("/api/settings", json={"max_rate": "fast"})
    assert r.status_code == 400
    r = c.post("/api/settings", json={"max_rate": "5M", "parallel": "3", "sync_every_hours": 6})
    s = r.json()["settings"]
    assert s["max_rate"] == "5M" and s["parallel"] == "3" and s["sync_every_hours"] == 6
    assert c.get("/api/status").json()["next_run"] is not None        # schedule is on


def test_download_folder_locked_in_docker(site, app_factory):
    c = app_factory(site, data_locked=True)
    before = c.get("/api/settings").json()["settings"]["data_dir"]
    c.post("/api/settings", json={"data_dir": "/somewhere/else"})
    after = c.get("/api/settings").json()
    assert after["settings"]["data_dir"] == before and after["data_locked"] is True


def test_password_protects_everything(site, app_factory):
    c = app_factory(site, password="hunter2")
    assert c.get("/").status_code == 401
    assert c.get("/api/status").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"any:hunter2").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"any:nope").decode()}
    assert c.get("/api/status", headers=bad).status_code == 401
    assert c.get("/api/status", headers=good).status_code == 200
    assert c.get("/", headers=good).status_code == 200


def test_interface_is_served(site, app_factory):
    c = app_factory(site)
    page = c.get("/")
    assert page.status_code == 200 and "Rom Sync" in page.text
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/static/app.css").status_code == 200
    import lolroms_sync
    assert c.get("/api/status").json()["version"] == lolroms_sync.__version__   # shown in the header


def test_ticking_every_subfolder_ticks_the_parent(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari/2600")
    assert kids(c)["Atari"]["state"] == "partial"
    for k in ("5200", "800", "Lynx"):
        select(c, f"Atari/{k}")
    assert kids(c)["Atari"]["state"] == "on"                          # a tick, not "-"
    assert all(k["state"] == "on" for k in kids(c, "Atari").values())
    select(c, "Atari/800/Flux", False)            # 800 holds nothing but Flux, so it is now unticked
    assert kids(c)["Atari"]["state"] == "partial" and kids(c, "Atari")["800"]["state"] == "off"
    select(c, "Atari/800/Flux", True)
    assert kids(c)["Atari"]["state"] == "on"


def test_sync_reports_its_total_size_and_what_is_left(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari/5200")
    st = sync(c)
    s = st["sync"]
    assert s["to_fetch"] == 2 and s["bytes_planned"] > 2_900_000      # Galaxian + Big.7z
    assert s["files_left"] == 0 and s["bytes_left"] == 0 and s["eta"] is None


def test_unfinished_sync_continues_after_a_restart(site_factory, app_factory, tmp_path):
    site = site_factory("slowfile")                                 # Big.7z trickles in
    c = app_factory(site)
    scan(c)
    select(c, "Atari/5200")
    assert c.post("/api/sync").status_code == 200
    t0 = time.time()
    while not any(p.endswith(".part") for p in on_disk(c.data)) and time.time() - t0 < 30:
        time.sleep(0.1)
    c.__exit__(None, None, None)                                    # app closed mid-download
    assert "Atari/5200/Big.7z" not in on_disk(c.data)

    c2 = app_factory(site)                                          # same config folder
    assert tree(c2, "Atari")["node"]["state"] == "partial"          # ticks survived
    t0 = time.time()
    while not c2.get("/api/status").json()["busy"] and time.time() - t0 < 15:
        time.sleep(0.1)                                             # it starts by itself
    wait(c2, 120)
    assert {"Atari/5200/Big.7z", "Atari/5200/Galaxian (USA).7z"} <= on_disk(c2.data)
    assert any("Range" in h for _, h in site.file_headers()), "the .part file was resumed"


def test_stopped_or_finished_sync_does_not_restart(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Acorn")
    sync(c)
    c.__exit__(None, None, None)
    c2 = app_factory(site)
    time.sleep(4)
    assert not c2.get("/api/status").json()["busy"]


def test_romm_page_and_choices(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari")
    sync(c)
    d = c.get("/api/romm").json()
    assert {p["slug"] for p in d["platforms"]} == {"atari2600", "atari5200"}   # 800 only has Flux dumps
    assert "roms/atari2600" in d["outputs"]["compose"]
    names = {p["slug"]: p["name"] for p in d["slugs"]}
    assert len(names) > 400 and names["satellaview"] == "Satellaview" and "Super Nintendo" in names["snes"]
    d = c.post("/api/romm/options", json={"host_path": "/path/to/roms"}).json()
    assert '"/path/to/roms/Atari/2600"' in d["outputs"]["compose"]
    d = c.post("/api/romm/map", json={"path": "Atari/2600", "action": "skip"}).json()
    assert "atari2600" not in {p["slug"] for p in d["platforms"]}
    assert c.post("/api/romm/map", json={"path": "Atari/2600", "action": "Not A Slug"}).status_code == 400
    assert c.get("/api/settings").json()["settings"]["romm"]["map"] == {"Atari/2600": "skip"}
    assert c.get("/api/romm").json()["options"]["layout"] == "category"
    d = c.post("/api/romm/options", json={"layout": "folders"}).json()
    assert d["options"]["layout"] == "folders" and "{category}" not in d["outputs"]["config"]


def test_restart_and_shutdown_endpoints(site, tmp_path):
    called = []
    app = create_app(lambda: Engine(str(tmp_path / "c"), str(tmp_path / "d"), site.url + "/", scan_delay=0),
                     on_restart=lambda: called.append("restart"), on_shutdown=lambda: called.append("shutdown"))
    with TestClient(app) as c:
        assert c.post("/api/restart").status_code == 200
        r = c.post("/api/shutdown")
        assert r.status_code == 200 and r.json()["docker"] is False
        time.sleep(0.6)
    assert called == ["restart", "shutdown"]
    with TestClient(create_app(lambda: Engine(str(tmp_path / "c"), str(tmp_path / "d"), site.url + "/"))) as c:
        assert c.post("/api/restart").status_code == 501
        assert c.post("/api/shutdown").status_code == 501


def test_downloaded_files_and_folders_get_the_permissions_setting(site, app_factory):
    import stat
    c = app_factory(site)
    old = os.umask(0o022)                   # a typical umask would give 644 files / 755 folders
    try:
        scan(c)
        select(c, "Atari/2600")
        sync(c)
    finally:
        os.umask(old)
    f = c.data / "Atari" / "2600" / "Adventure (USA).7z"
    assert stat.S_IMODE(f.stat().st_mode) == 0o777                         # default setting: 777
    assert stat.S_IMODE((c.data / "Atari" / "2600").stat().st_mode) == 0o777
    assert stat.S_IMODE((c.data / "Atari").stat().st_mode) == 0o777

    assert c.post("/api/settings", json={"file_mode": "00775"}).json()["settings"]["file_mode"] == "775"
    assert c.post("/api/settings", json={"file_mode": "999"}).status_code == 400
    os.chmod(f, 0o600)
    sync(c)                                                                # files already there are fixed too
    assert stat.S_IMODE(f.stat().st_mode) == 0o775


def test_files_and_folders_get_the_website_dates(site, app_factory):
    c = app_factory(site)
    scan(c)
    select(c, "Atari/2600")
    sync(c)
    files = list((c.data / "Atari" / "2600").glob("*.7z"))
    assert files and all(time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime)) == "2025-11-17"
                         for f in files)                                   # the website's date
    newest = max(f.stat().st_mtime for f in files)
    for folder in (c.data / "Atari" / "2600", c.data / "Atari"):           # folders too, not "today"
        assert abs(folder.stat().st_mtime - newest) < 2, folder


def test_files_in_a_folder_without_its_subfolders(site, app_factory):
    site.files["Atari/800"] = ["Star Raiders.7z"]                  # files of its own + subfolder Flux
    c = app_factory(site)
    scan(c)
    select(c, "Atari/800/Flux")                                    # ticking the subfolder...
    n = tree(c, "Atari/800")["node"]
    assert n["state"] == "partial" and n["own_state"] == "off"     # ...leaves the folder's own files out
    r = c.post("/api/select", json={"path": "Atari/800/.", "include": True})   # "Files in this folder"
    assert r.status_code == 200
    assert kids(c, "Atari")["800"]["state"] == "on"                # own files + every subfolder = all
    select(c, "Atari/800/Flux", False)
    n = tree(c, "Atari/800")["node"]
    assert n["state"] == "partial" and n["own_state"] == "on"
    sync(c)
    got = on_disk(c.data)
    assert "Atari/800/Star Raiders.7z" in got
    assert not any(p.startswith("Atari/800/Flux/") for p in got)
    assert c.post("/api/select", json={"path": "Nope/.", "include": True}).status_code == 404


def test_romm_links_follow_each_sync(site, app_factory, tmp_path):
    c = app_factory(site)                                        # downloads: tmp/data, links: tmp
    scan(c)
    select(c, "Atari/2600")
    c.post("/api/romm/options", json={"method": "links", "links_dir": str(tmp_path)})
    sync(c)
    link = tmp_path / "atari2600"
    assert link.is_symlink() and os.readlink(link) == "data/Atari/2600"
    c.post("/api/romm/map", json={"path": "Atari/2600", "action": "skip"})   # no longer for RomM
    d = c.post("/api/romm/links", json={}).json()
    assert d["applied"]["removed"] == ["atari2600"] and not link.exists()
