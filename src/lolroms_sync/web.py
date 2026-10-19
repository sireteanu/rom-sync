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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
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
            "own_state": "on" if engine.catalog.effective_files(path, rules) else "off",
            **_saved_as(engine, path)}


def _saved_as(engine: Engine, path: str) -> dict:
    """The tree's "Saved as" column: what the field shows (the folder's own setting, or its site
    name), and its whole place on disk."""
    if not path:
        return {"place": "", "custom": False, "disk_path": "", "moved": False}
    fm = engine.fmap
    disk = fm.disk_folder(path)
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    place = fm.place_of(path)
    return {"place": place or path.rsplit("/", 1)[-1], "custom": bool(place), "disk_path": disk,
            # not in its parent's folder on disk (its own / path, or its parent's subfolder setting)
            "moved": (disk.rsplit("/", 1)[0] if "/" in disk else "") != fm.disk_folder(parent)}


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

    # folder trees, search results and file lists are large JSON that compresses well
    app.add_middleware(GZipMiddleware, minimum_size=1024)

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
        # the page asks for its script and styles by version, so a browser never runs an old
        # cached app.js against a newer page after an update
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            html = f.read()
        for name in ("app.js", "app.css"):
            html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={__version__}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

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
        if not e.catalog.has_dir(folder) or (path.endswith(OWN) and not folder):
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

    async def _place(body: dict, kids: bool):
        try:
            return eng().set_place(body.get("path") or "", body.get("place"), kids=kids)
        except RuntimeError:
            return JSONResponse({"error": "Wait until the scan or sync has finished, then try again."},
                                status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/folder-place")
    async def folder_place(body: dict = Body(...)):
        return await _place(body, kids=False)

    @app.get("/api/history")
    async def history():
        return {"history": eng().catalog.history(20)}

    # ---------------------------------------------------------------- what changed
    SYNC_KINDS = ("new", "updated", "resumed", "uptodate", "failed")

    @app.get("/api/sync/files")
    async def sync_files(kind: str, limit: int = 2000):
        """The files behind one of the Activity counters, for the current or last sync."""
        e = eng()
        if kind not in SYNC_KINDS:
            raise HTTPException(400, "kind must be one of " + ", ".join(SYNC_KINDS))
        job = e.job if e.job and e.job.get("kind") in ("sync", "scan+sync") else None
        if job is None:
            return {"total": 0, "files": [], "job": None}
        if kind == "uptodate":
            total, rows = e.catalog.unchanged_since(job["started"], limit)
        else:
            total, rows = e.catalog.changes(job=job["started"], kind=kind, limit=limit)
        return {"total": total, "files": rows, "job": job["started"]}

    @app.get("/api/changes/days")
    async def change_days():
        return {"days": eng().catalog.change_days()}

    @app.get("/api/changes")
    async def changes(day: str = "", kind: str = "", limit: int = 2000):
        total, rows = eng().catalog.changes(day=day or None, kind=kind or None, limit=limit)
        return {"total": total, "files": rows}

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
