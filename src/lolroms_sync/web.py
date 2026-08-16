"""HTTP API + the single-page interface."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import logging
import secrets
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import romm
from .catalog import OWN
from .engine import Engine, parse_types

log = logging.getLogger("dircrawl")

STATIC = os.path.join(os.path.dirname(__file__), "static")


def node_json(engine: Engine, tree: dict, rules: dict, path: str) -> dict:
    n = tree[path]
    return {"path": n.path, "name": n.name or "lolroms.com", "files": n.files, "bytes": n.bytes,
            "newest": n.newest, "dirs": n.dirs, "has_children": bool(n.children),
            "own_files": n.own_files, "synced": n.synced, "synced_bytes": n.synced_bytes,
            "failed": n.failed, "error": n.error, "scanned": n.scanned,
            "state": engine.catalog.state_of(path, rules),
            # the files directly in this folder (shown as their own row when it also has subfolders)
            "own_bytes": n.own_bytes, "own_newest": n.own_newest, "own_synced": n.own_synced,
            "own_synced_bytes": n.own_synced_bytes, "own_failed": n.own_failed,
            "own_state": "on" if engine.catalog.effective_files(path, rules) else "off"}


def create_app(engine_factory, password: Optional[str] = None, on_restart=None, on_shutdown=None) -> FastAPI:
    """on_restart / on_shutdown: called by POST /api/restart and /api/shutdown to restart or end the
    whole program (see __main__). Both keep an unfinished sync, which continues at the next start."""
    holder: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(app):
        engine = engine_factory()
        holder["engine"] = engine
        engine.scheduler = asyncio.create_task(engine.scheduler_loop())
        try:
            yield
        finally:
            engine.shutdown()
            engine.scheduler.cancel()
            if engine.task:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(engine.task, 10)
            engine.catalog.close()

    app = FastAPI(title="lolroms-sync", lifespan=lifespan, docs_url=None, redoc_url=None)

    def eng() -> Engine:
        return holder["engine"]

    if password:
        @app.middleware("http")
        async def basic_auth(request: Request, call_next):
            if request.url.path == "/api/health":          # Docker health check
                return await call_next(request)
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(given, password)
                except Exception:
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="lolroms-sync"'})
            return await call_next(request)

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/status")
    async def status(log_since: int = 0):
        return eng().status(log_since)

    @app.get("/api/tree")
    async def tree(path: str = ""):
        e = eng()
        types = parse_types(e.catalog.settings()["types"])
        t = e.catalog.tree(types)
        if path not in t:
            raise HTTPException(404, "no such folder")
        rules = e.catalog.rules()
        return {"node": node_json(e, t, rules, path),
                "children": [node_json(e, t, rules, c) for c in t[path].children]}

    @app.get("/api/search")
    async def search(q: str = ""):
        e = eng()
        types = parse_types(e.catalog.settings()["types"])
        t = e.catalog.tree(types)
        rules = e.catalog.rules()
        return {"results": [node_json(e, t, rules, p) for p in e.catalog.search(q) if p in t]}

    @app.get("/api/files")
    async def files(path: str = "", limit: int = 2000):
        e = eng()
        with e.catalog.lock:
            rows = e.catalog.db.execute(
                "SELECT name, rel, ext, date, size, local, local_size, error FROM files WHERE dir=? "
                "ORDER BY name COLLATE NOCASE LIMIT ?", (path, limit)).fetchall()
            total = e.catalog.db.execute("SELECT COUNT(*) FROM files WHERE dir=?", (path,)).fetchone()[0]
        keys = ("name", "rel", "ext", "date", "size", "local", "local_size", "error")
        return {"total": total, "files": [dict(zip(keys, r)) for r in rows]}

    @app.post("/api/select")
    async def select(path: str = Body(...), include: bool = Body(...)):
        e = eng()
        folder = path[:-len(OWN)] if path.endswith(OWN) else path     # "<folder>/." = its own files
        if folder not in e.catalog.tree() or (path.endswith(OWN) and not folder):
            raise HTTPException(404, "no such folder")
        e.catalog.select(path, include)
        return {"ok": True}

    @app.post("/api/selection/clear")
    async def clear_selection():
        eng().catalog.clear_selection()
        return {"ok": True}

    @app.post("/api/scan")
    async def scan(root: str = Body("", embed=True)):
        if not eng().start("scan", root):
            raise HTTPException(409, "already busy")
        return {"ok": True}

    @app.post("/api/sync")
    async def sync():
        if not eng().start("sync"):
            raise HTTPException(409, "already busy")
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        eng().stop()
        return {"ok": True}

    @app.get("/api/settings")
    async def get_settings():
        e = eng()
        return {"settings": e.catalog.settings(), "data_locked": e.data_locked}

    @app.post("/api/settings")
    async def set_settings(values: dict = Body(...)):
        try:
            return {"settings": eng().update_settings(values), "data_locked": eng().data_locked}
        except (ValueError, Exception) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/history")
    async def history():
        return {"history": eng().catalog.history(20)}

    # ---------------------------------------------------------------- clean up
    @app.get("/api/cleanup")
    async def cleanup_list():
        e = eng()
        files = await e.find_leftovers()
        return {"files": files, "summary": e.leftovers, "busy": e.busy}

    @app.post("/api/cleanup/delete")
    async def cleanup_delete(paths: list = Body(..., embed=True), confirm: bool = Body(False, embed=True)):
        e = eng()
        if not confirm:
            raise HTTPException(400, "confirm the delete first")
        if e.busy:
            raise HTTPException(409, "wait until the scan or sync has finished, then try again")
        res = await e.delete_leftovers([str(p) for p in paths])
        return dict(res, summary=e.leftovers)

    # ---------------------------------------------------------------- RomM mapping
    def romm_view(e: Engine) -> dict:
        s = e.catalog.settings()
        opt = romm.options_from(s)
        m = romm.Mapping(romm.folders_from_catalog(e.catalog, parse_types(s["types"])), opt, s["data_dir"])
        return {"options": {**{k: opt[k] for k in ("only_synced", "variants", "layout", "links_dir",
                                                 "keep_links")}, "romm_folder": m.romm_folder()},
                "links": {"dir": m.links_dir, "download_dir": m.data_dir, "inside": m.data_in_links(),
                          "targets": m.link_targets()},
                "rows": m.rows(), "platforms": m.platforms, "firmware": m.firmware,
                "warnings": m.warnings(), "summary": m.summary(), "slugs": [{"slug": k, "name": v[0], "family": v[1], "kind": v[2]}
                          for k, v in sorted(romm.PLATFORMS.items())],
                "outputs": {"compose": m.compose(), "config": m.config_yml()}}

    def save_romm(e: Engine, cur: dict) -> None:
        """Save RomM Mapper choices in the catalog, plus a backup file next to it that is restored
        if the catalog is ever reset or replaced."""
        e.catalog.save_settings({"romm": cur})
        romm.write_backup(e.config_dir, cur)

    @app.get("/api/romm")
    async def romm_get():
        return romm_view(eng())

    @app.post("/api/romm/options")
    async def romm_options(values: dict = Body(...)):
        e = eng()
        cur = dict(e.catalog.settings().get("romm") or {})
        for k in ("romm_folder", "links_dir"):
            if k in values:
                cur[k] = str(values[k] or "").strip()
        for k in ("only_synced", "variants", "keep_links"):
            if k in values:
                cur[k] = bool(values[k])
        if values.get("layout") in ("category", "folders"):
            cur["layout"] = values["layout"]
        for old in ("host_path", "library", "method"):     # settings from before 1.3.0
            if old == "method" and cur.get(old) == "links":
                cur.setdefault("keep_links", True)
            cur.pop(old, None)
        save_romm(e, cur)
        return romm_view(e)

    @app.post("/api/romm/map")
    async def romm_map(path: str = Body(...), action: str = Body("")):
        e = eng()
        try:
            action = romm.check_action(action)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        cur = dict(e.catalog.settings().get("romm") or {})
        mapping = dict(cur.get("map") or {})
        if action:
            mapping[path] = action
        else:
            mapping.pop(path, None)
        cur["map"] = mapping
        save_romm(e, cur)
        return romm_view(e)

    @app.post("/api/romm/links")
    async def romm_links():
        e = eng()
        s = e.catalog.settings()
        m = romm.Mapping(romm.folders_from_catalog(e.catalog, parse_types(s["types"])),
                         romm.options_from(s), s["data_dir"])
        res = await asyncio.to_thread(romm.apply_links, m)
        log.info(romm.describe_links(res))
        if m.data_in_links() is not None:                 # from now on, kept up to date after each sync
            cur = dict(s.get("romm") or {})
            if not cur.get("keep_links"):
                cur["keep_links"] = True
                save_romm(e, cur)
        view = romm_view(e)
        view["applied"] = dict(res, text=romm.describe_links(res))
        return view

    # ---------------------------------------------------------------- restart
    @app.post("/api/restart")
    async def restart():
        if on_restart is None:
            raise HTTPException(501, "restart is not available when started this way")
        log.info("Restarting Rom Sync...")
        asyncio.get_running_loop().call_later(0.3, on_restart)
        return {"ok": True}

    @app.post("/api/shutdown")
    async def shutdown():
        if on_shutdown is None:
            raise HTTPException(501, "shutting down is not available when started this way")
        log.info("Shutting down Rom Sync (from the web page)...")
        asyncio.get_running_loop().call_later(0.3, on_shutdown)
        return {"ok": True, "docker": eng().data_locked}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app
