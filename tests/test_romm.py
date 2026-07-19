"""RomM mapping: lolroms folders -> RomM mounts + config.yml, without moving anything."""
import pytest

from lolroms_sync.romm import Mapping, check_action

# (files in folder, of which synced) - shaped like the real site
FOLDERS = {
    "": (0, 0), "Nintendo": (0, 0),
    "Nintendo/Super Famicom": (10, 10), "Nintendo/Super Famicom/Satellaview": (5, 5),
    "Nintendo/Super Famicom/T-En": (3, 3), "Nintendo/Super Famicom/MSU-1": (2, 0),
    "Nintendo/GameCube": (4, 4), "Nintendo/GameCube/BIOS": (1, 1), "Nintendo/GameCube/Saves": (2, 2),
    "APF": (0, 0), "APF/Imagination Machine": (3, 3), "APF/MP-1000": (2, 2),
    "Apple": (0, 0), "Apple/II": (0, 0), "Apple/II/A2R": (9, 9), "Apple/II/WOZ": (2, 2),
    "Konami - Picno": (4, 4),
    "Arcade": (0, 0), "Arcade/Naomi": (6, 6), "Arcade/MAME": (8, 8), "Arcade/FBNeo": (8, 8),
}


def m(**opt):
    return Mapping(FOLDERS, dict({"host_path": "/path/to/roms", "layout": "folders"}, **opt))


def cat(folders=None, **opt):
    return Mapping(folders or FOLDERS, dict({"host_path": "/path/to/roms"}, **opt))   # default layout


def plat(mp, slug):
    return next(p for p in mp.platforms if p["slug"] == slug)


def test_nested_platforms_get_their_own_mount():
    mp = m()
    snes, sat = plat(mp, "snes"), plat(mp, "satellaview")
    assert snes["mount"] == "Nintendo/Super Famicom" and sat["mount"] == "Nintendo/Super Famicom/Satellaview"
    assert snes["templates"] == ["roms/{platform}/{game}", "roms/{platform}/T-En/{game}"]   # MSU-1 not synced
    assert "Satellaview" in snes["exclude"] and "MSU-1" in snes["exclude"]
    out = mp.compose()
    assert '"/path/to/roms/Nintendo/Super Famicom"' in out and '"/romm/library/roms/snes"' in out
    assert "read_only: true" in out


def test_firmware_and_skipped_subfolders():
    mp = m()
    assert plat(mp, "ngc")["templates"] == ["roms/{platform}/{game}"]
    assert set(plat(mp, "ngc")["exclude"]) == {"BIOS", "Saves"}
    assert [f["mount"] for f in mp.firmware] == ["Nintendo/GameCube/BIOS"]
    assert "/romm/library/bios/ngc" in mp.compose()


def test_siblings_with_one_platform_share_a_mount():
    apf = plat(m(), "apf")
    assert apf["mount"] == "APF"
    assert apf["templates"] == ["roms/{platform}/Imagination Machine/{game}", "roms/{platform}/MP-1000/{game}"]
    # flux captures are skipped, WOZ images are kept
    assert plat(m(), "appleii")["mount"] == "Apple/II/WOZ"


def test_config_yml_sections():
    y = m().config_yml()
    assert 'exclude:' in y and '- "Satellaview"' in y
    assert '"snes":' in y and '- "roms/{platform}/T-En/{game}"' in y
    assert '"ngc":' not in y                                     # default layout needs no override
    assert 'versions:' in y and '"naomi": "arcade"' in y
    assert '"arcade"' in y or "roms/arcade" in m().compose()
    assert not any(p["slug"] == "fbneo" for p in m().platforms)


def test_unmapped_and_user_choices():
    mp = m()
    row = next(r for r in mp.rows() if r["path"] == "Konami - Picno")
    assert row["kind"] == "unmapped" and any("Konami - Picno" in w for w in mp.warnings())
    mp = m(map={"Konami - Picno": "picno", "Nintendo/Super Famicom/T-En": "skip",
                "Arcade/FBNeo": "merge"})
    assert plat(mp, "picno")["mount"] == "Konami - Picno"
    assert any("picno" in w for w in mp.warnings())                # not a RomM slug
    assert plat(mp, "snes")["templates"] == ["roms/{platform}/{game}"]
    assert not any(r["path"] == "Arcade/FBNeo" and r["kind"] == "merge" for r in mp.rows())  # nothing to merge into


def test_variants_off_and_all_files():
    assert plat(m(variants=False), "snes")["templates"] == ["roms/{platform}/{game}"]
    assert "roms/{platform}/MSU-1/{game}" in plat(m(only_synced=False), "snes")["templates"]


def test_unraid_extra_parameters():
    u = m().unraid()
    assert "--mount 'type=bind,source=/path/to/roms/Nintendo/Super Famicom,target=/romm/library/roms/snes,readonly'" in u


def test_check_action():
    assert check_action(" SNES ") == "snes" and check_action("") == "" and check_action("firmware:psx") == "bios:psx"
    for bad in ("super famicom", "bios:", "../x"):
        with pytest.raises(ValueError):
            check_action(bad)


def test_category_layout_is_the_default_and_uses_wildcards():
    mp = cat()
    snes = plat(mp, "snes")
    assert snes["templates"] == ["roms/{platform}/{game}", "roms/{platform}/{category}/{game}"]
    assert {"Satellaview", "MSU-1"} <= set(snes["exclude"])          # kept out of the wildcard
    apf = plat(mp, "apf")                                            # mount APF, games one level down
    assert apf["templates"] == ["roms/{platform}/{category}/{game}"]
    assert plat(mp, "ngc")["templates"] == ["roms/{platform}/{game}"]
    y = mp.config_yml()
    assert '- "roms/{platform}/{category}/{game}"' in y and '"Satellaview"' in y


def test_category_layout_two_levels_and_mount_children_excluded():
    folders = {"": (0, 0), "SEGA": (0, 0), "SEGA/Saturn": (5, 5), "SEGA/Saturn/T-En": (0, 0),
               "SEGA/Saturn/T-En/Discs": (3, 3), "SEGA/Saturn/Flux": (2, 2)}
    sat = plat(cat(folders), "saturn")
    assert sat["templates"] == ["roms/{platform}/{game}", "roms/{platform}/{category}/{subcategory}/{game}"]
    assert sat["exclude"] == ["Flux"]


def test_category_layout_falls_back_to_exact_names_when_a_wildcard_would_misread():
    # a folder needed by one platform has a name another platform excludes -> exact name added
    folders = {"": (0, 0), "A": (2, 2), "A/Extras": (1, 1), "B": (2, 2), "B/Extras": (1, 1)}
    mp = cat(folders, map={"A": "nes", "B": "snes", "B/Extras": "skip"})
    nes = plat(mp, "nes")
    assert "roms/{platform}/Extras/{game}" in nes["templates"]
    # a grouping folder with unwanted files of its own at a wildcard depth -> exact names only
    folders = {"": (0, 0), "P": (0, 0), "P/X": (2, 2), "P/Y": (2, 2), "P/Y/Z": (2, 2)}
    mp = cat(folders, map={"P/X": "gb", "P/Y/Z": "gb", "P/Y": "skip"})
    gb = plat(mp, "gb")
    assert gb["templates"] == ["roms/{platform}/X/{game}", "roms/{platform}/Y/Z/{game}"]


def test_links_mode_makes_relative_platform_links(tmp_path):
    import os
    from lolroms_sync.romm import apply_links
    lib = tmp_path / "romm"                                    # RomM's roms folder = /path/to/roms
    data = lib / "lolroms"                                     # Rom Sync's downloads inside it
    for p in FOLDERS:
        (data / p).mkdir(parents=True, exist_ok=True)
    (lib / "gb").mkdir()                                       # a real folder RomM already has
    os.symlink("elsewhere/x", lib / "psx")                     # someone else's link
    os.symlink("lolroms/Old/Gone", lib / "oldslug")            # a stale link of ours
    mp = Mapping(FOLDERS, {"host_path": "/path/to/roms/lolroms", "method": "links"}, str(data))
    assert mp.data_in_links() == "lolroms"
    t = mp.link_targets()
    assert t["snes"] == "lolroms/Nintendo/Super Famicom" and t["apf"] == "lolroms/APF"
    res = apply_links(mp)
    assert not res["errors"] and "snes" in res["created"] and res["removed"] == ["oldslug"]
    assert os.readlink(lib / "snes") == "lolroms/Nintendo/Super Famicom"           # relative
    assert (lib / "snes").resolve() == (data / "Nintendo" / "Super Famicom").resolve()
    assert os.readlink(lib / "psx") == "elsewhere/x" and (lib / "gb").is_dir()     # untouched
    assert apply_links(mp)["kept"] == len(t)                                       # idempotent
    y = mp.config_yml()
    assert 'platforms:\n    - "lolroms"' in y and '"Satellaview"' in y
    assert '"/path/to/roms:/romm/library/roms"' in mp.compose()


def test_links_mode_needs_the_downloads_inside_the_links_folder(tmp_path):
    mp = Mapping(FOLDERS, {"method": "links", "links_dir": str(tmp_path / "other")}, str(tmp_path / "dl"))
    assert mp.data_in_links() is None and mp.link_targets() == {}
    assert any("inside the links folder" in w for w in mp.warnings())
