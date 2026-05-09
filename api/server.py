# api/server.py

import asyncio
import logging
import multiprocessing as mp
import os
import threading
import time
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from api.dashboard_routes import dashboard_router
from api.analytics_routes import analytics_router
from api.object_routes    import object_router
import api.state as state
from core.logger_setup   import log_system, log_error
from core.stream_manager import inference_worker

logger = logging.getLogger("ObjectTrackAI")

# ── Streaming constants ────────────────────────────────────────────────────────
_TARGET_STREAM_FPS    = 20
_STREAM_FRAME_INTERVAL = 1.0 / _TARGET_STREAM_FPS   # 50 ms per frame
_STATS_INTERVAL       = 0.5    # 2 Hz  (was 0.1 / 10 Hz)
_SEND_TIMEOUT         = 3.0    # seconds before we consider a send stalled

# ── Watchdog ───────────────────────────────────────────────────────────────────
_WATCHDOG_INTERVAL = 10.0


# ── System bootstrap ───────────────────────────────────────────────────────────

def init_system(manager: mp.managers.SyncManager, log_queue) -> None:
    """
    Initialise shared state and spawn one inference worker per active line.
    Must be called BEFORE uvicorn.run() so the DictProxy exists before any
    FastAPI request arrives.
    """
    print("[INIT] Starting system initialisation…")

    res_dict = manager.dict()
    state.init_state(manager, res_dict)

    config_dir    = os.environ.get("CONFIG_DIR", "config")
    settings_path = os.path.join(config_dir, "settings.yaml")
    cameras_path  = os.path.join(config_dir, "cameras.yaml")

    print(f"[INIT] Loading settings from {settings_path}")
    with open(settings_path, "r") as fh:
        settings = yaml.safe_load(fh)

    print(f"[INIT] Loading cameras from {cameras_path}")
    with open(cameras_path, "r") as fh:
        cameras = yaml.safe_load(fh)

    state.line_configs = {}
    for line in cameras["conveyor_lines"]:
        if not line.get("active", True):
            continue
        lid = line["id"]
        state.line_configs[lid] = line

        print(f"[INIT] Starting worker for line {lid}")
        p = mp.Process(
            target=inference_worker,
            args=(line, settings, res_dict, log_queue),
            name=f"worker-line-{lid}",
            daemon=True,
        )
        p.start()
        state.processes.append((lid, p))
        log_system(f"Worker for line {lid} started (pid={p.pid})")

    print(f"[INIT] {len(state.processes)} worker(s) started.")
    _start_watchdog(settings, res_dict, log_queue)


def _start_watchdog(settings: dict, res_dict, log_queue) -> None:
    def _loop() -> None:
        while True:
            time.sleep(_WATCHDOG_INTERVAL)
            for lid, proc in state.processes[:]:
                if not proc.is_alive():
                    log_system(
                        f"Watchdog: line {lid} worker dead (pid={proc.pid}), restarting…"
                    )
                    state.processes.remove((lid, proc))
                    line_cfg = state.line_configs.get(lid)
                    if line_cfg is None:
                        log_error(f"Watchdog: cannot restart line {lid} – config missing")
                        continue
                    new_proc = mp.Process(
                        target=inference_worker,
                        args=(line_cfg, settings, res_dict, log_queue),
                        name=f"worker-line-{lid}",
                        daemon=True,
                    )
                    new_proc.start()
                    state.processes.append((lid, new_proc))
                    log_system(
                        f"Watchdog: line {lid} restarted (pid={new_proc.pid})"
                    )

    t = threading.Thread(target=_loop, daemon=True, name="worker-watchdog")
    t.start()
    log_system("Worker watchdog thread started")


# ── FastAPI lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log_system("FastAPI application starting")
    yield
    log_system("Shutting down – terminating workers")
    for lid, proc in state.processes:
        proc.terminate()
        proc.join(timeout=3)
        if proc.is_alive():
            proc.kill()
            log_system(f"Worker for line {lid} force-killed")
        else:
            log_system(f"Worker for line {lid} terminated cleanly")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="ObjectTrackAI", version="1.2.0", lifespan=lifespan)

app.mount("/web", StaticFiles(directory="web"), name="web")
app.include_router(dashboard_router)
app.include_router(analytics_router)
app.include_router(object_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/web/index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/api/health")
async def health():
    """Quick liveness check for monitoring / load balancers."""
    rd = state.get_result_dict()
    worker_status = {}
    for lid, proc in state.processes:
        worker_status[str(lid)] = "alive" if proc.is_alive() else "dead"
    return JSONResponse({
        "status": "ok",
        "workers": worker_status,
        "result_dict_ready": rd is not None,
    })


# ── WebSocket helpers ──────────────────────────────────────────────────────────

async def _safe_send_bytes(ws: WebSocket, data: bytes) -> bool:
    """
    Send binary data with a timeout.

    Returns True on success, False if the send timed out or the connection
    is no longer open.  Avoids the AssertionError in websockets keepalive_ping
    that occurs when send_bytes is called while the socket is draining a
    previous frame (backpressure).
    """
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await asyncio.wait_for(ws.send_bytes(data), timeout=_SEND_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        log_error("WebSocket send timed out – client too slow, dropping connection")
        return False
    except Exception:
        return False


async def _safe_send_json(ws: WebSocket, payload: dict) -> bool:
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await asyncio.wait_for(ws.send_json(payload), timeout=_SEND_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        return False
    except Exception:
        return False


# ── WebSocket: video stream ────────────────────────────────────────────────────

@app.websocket("/ws/stream/{line_id}")
async def video_stream(websocket: WebSocket, line_id: str):
    await websocket.accept()
    log_system(f"Stream client connected for line {line_id}")

    rd          = state.get_result_dict()
    frame_count = 0
    last_frame_hash = None          # skip identical frames (static scene)

    try:
        while True:
            # Pace the loop at target FPS.  asyncio.sleep yields the event
            # loop to other coroutines (stats ws, HTTP handlers) each iteration.
            await asyncio.sleep(_STREAM_FRAME_INTERVAL)

            if rd is None:
                continue

            data = rd.get(line_id)
            if not data or "frame" not in data:
                continue

            frame_bytes: bytes = data["frame"]

            # Skip sending identical frames (e.g. static placeholder)
            fhash = len(frame_bytes)          # cheap proxy; good enough
            if fhash == last_frame_hash and frame_count > 0:
                continue
            last_frame_hash = fhash

            ok = await _safe_send_bytes(websocket, frame_bytes)
            if not ok:
                break

            frame_count += 1

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_error(f"Stream error for line {line_id}", exc=exc)
    finally:
        log_system(
            f"Stream client disconnected for line {line_id} "
            f"after {frame_count} frames"
        )


# ── WebSocket: live stats ──────────────────────────────────────────────────────

@app.websocket("/ws/stats")
async def stats_stream(websocket: WebSocket):
    await websocket.accept()
    log_system("Stats client connected")
    rd = state.get_result_dict()

    try:
        while True:
            await asyncio.sleep(_STATS_INTERVAL)   # 2 Hz

            if rd is None:
                continue

            payload: dict = {}
            for line_id, data in rd.items():
                if data and "stats" in data:
                    payload[line_id] = {"stats": data["stats"]}

            ok = await _safe_send_json(websocket, payload)
            if not ok:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_error("Stats stream error", exc=exc)
    finally:
        log_system("Stats client disconnected")