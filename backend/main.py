import os
import sys
import json
import asyncio
import logging
import threading
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from .yummy_client import YummyClient
from .kodik_parser import resolve_kodik_streams
from .sibnet_resolver import resolve_sibnet_streams
from .downloader import download_video
from .models import (
    ResolveRequest,
    DownloadRequest,
)

LOG_PATH = Path(__file__).parent.parent / "debug.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH), mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("downloader")

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def _is_termux():
    return "/data/data/com.termux" in sys.executable or Path.home().joinpath(".termux").exists()

def _default_download_dir():
    if _is_termux():
        return str(Path.home() / "storage" / "downloads" / "YummyAnime")
    return str(Path(__file__).parent.parent / "downloads")

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_token() -> str:
    cfg = load_config()
    return cfg.get("yummy_app_token", "")

def load_download_dir() -> str:
    cfg = load_config()
    return cfg.get("download_dir", _default_download_dir())


APP_TOKEN = load_token()

client: YummyClient | None = None
active_downloads: dict[str, dict] = {}
ws_connections: list[WebSocket] = []
download_dir: str = load_download_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, download_dir
    client = YummyClient(APP_TOKEN)
    download_dir = load_download_dir()
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    if APP_TOKEN:
        log.info(f"Using API token: {APP_TOKEN[:8]}...")
    else:
        log.info("Running without API token (works for basic use)")
    log.info(f"Download directory: {download_dir}")
    yield


app = FastAPI(title="YummyAnime Downloader", lifespan=lifespan)

frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/api/search")
async def search_anime(q: str, limit: int = 20):
    try:
        results = await client.search(q, limit)
        return {"data": [r.model_dump() for r in results]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/settings")
async def get_settings():
    cfg = load_config()
    return {
        "download_dir": cfg.get("download_dir", _default_download_dir()),
    }


@app.post("/api/settings")
async def update_settings(data: dict):
    global download_dir
    cfg = load_config()
    if "download_dir" in data:
        new_dir = data["download_dir"].strip()
        if new_dir:
            cfg["download_dir"] = new_dir
            download_dir = new_dir
            Path(new_dir).mkdir(parents=True, exist_ok=True)
            log.info(f"Download directory changed to: {new_dir}")
    save_config(cfg)
    return {"download_dir": cfg.get("download_dir", _default_download_dir())}


@app.get("/api/anime/{anime_url:path}")
async def get_anime(anime_url: str):
    try:
        detail = await client.get_anime_detail(anime_url)
        return {"data": detail.model_dump()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/resolve")
async def resolve_streams(req: ResolveRequest):
    player = req.player.lower() if req.player else _detect_player(req.iframe_url)
    log.info(f"Resolving streams: player={player}, url={req.iframe_url[:80]}...")

    try:
        if "kodik" in player:
            streams = await resolve_kodik_streams(req.iframe_url)
        elif "sibnet" in player:
            streams = await resolve_sibnet_streams(req.iframe_url)
        else:
            return JSONResponse(
                {"error": f"Неизвестный плеер: {player}"},
                status_code=400,
            )

        log.info(f"Found {len(streams)} streams: {[s.quality for s in streams]}")
        return {"data": [s.model_dump() for s in streams]}
    except Exception as e:
        log.error(f"Resolve failed: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


def _detect_player(iframe_url: str) -> str:
    url = iframe_url.lower()
    if "kodik" in url or "solod" in url or "stonehenge" in url:
        return "kodik"
    if "sibnet" in url or "sybmedia" in url:
        return "sibnet"
    return "unknown"


@app.post("/api/download")
async def start_download(req: DownloadRequest):
    global download_dir
    download_id = f"{req.output_path}_{id(req)}"
    output_path = str(Path(download_dir) / req.output_path)

    log.info(f"Starting download: {req.output_path} from {req.url[:80]}...")

    active_downloads[download_id] = {
        "status": "starting",
        "percent": 0,
        "filename": req.output_path,
    }

    loop = asyncio.get_event_loop()

    def progress_cb(info):
        active_downloads[download_id].update(info)
        if info.get("status") == "downloading":
            log.debug(f"Progress: {req.output_path} - {info.get('percent', 0)}%")
        try:
            loop.call_soon_threadsafe(
                asyncio.ensure_future,
                broadcast_progress(download_id, info),
            )
        except RuntimeError:
            pass

    def run_download():
        try:
            download_video(
                url=req.url,
                output_path=output_path,
                referer=req.referer,
                cookies=req.cookies or {},
                extra_headers=req.extra_headers or {},
                progress_callback=progress_cb,
            )
            log.info(f"Download complete: {req.output_path}")
            active_downloads[download_id].update({
                "status": "done",
                "percent": 100,
            })
            try:
                loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    broadcast_progress(download_id, {"status": "done", "percent": 100}),
                )
            except RuntimeError:
                pass
        except Exception as e:
            log.error(f"Download failed: {req.output_path} - {e}", exc_info=True)
            active_downloads[download_id].update({
                "status": "error",
                "error": str(e),
            })
            try:
                loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    broadcast_progress(download_id, {"status": "error", "error": str(e)}),
                )
            except RuntimeError:
                pass

    thread = threading.Thread(target=run_download, daemon=True)
    thread.start()

    return {"download_id": download_id, "status": "started"}


@app.get("/api/downloads")
async def list_downloads():
    global download_dir
    files = []
    dl_path = Path(download_dir)
    if dl_path.exists():
        for f in dl_path.iterdir():
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "path": str(f),
                })
    return {"data": files}


@app.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket):
    await ws.accept()
    ws_connections.append(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"status": "pong"}))
    except WebSocketDisconnect:
        ws_connections.remove(ws)


async def broadcast_progress(download_id: str, info: dict):
    message = json.dumps({"download_id": download_id, **info})
    dead = []
    for ws in ws_connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_connections.remove(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
