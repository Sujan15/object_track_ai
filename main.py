# main.py — application entry point
import logging
import multiprocessing as mp
import os
import uvicorn

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

from core.logger_setup import start_log_listener, log_app_start, log_app_stop
from api.server import app, init_system

def main() -> None:
    mp.set_start_method("spawn", force=True)
    manager = mp.Manager()
    log_queue = mp.Queue(maxsize=2_000_000)

    start_log_listener(log_queue)
    log_app_start()
    init_system(manager, log_queue)

    host = os.environ.get("HOST", "localhost")
    port = int(os.environ.get("PORT", "8000"))

    logging.basicConfig(level=logging.INFO)
    print(f"[MAIN] Starting server on http://{host}:{port}", flush=True)

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=True,
            timeout_keep_alive=65,
            ws_ping_interval=None,      # Disable automatic ping
            ws_ping_timeout=None,
        )
    finally:
        log_app_stop()

asgi_app = app

if __name__ == "__main__":
    main()