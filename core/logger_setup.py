# core/logger_setup.py
"""
Centralised logging for ObjectTrackAI.

Log record format (set by every handler):
    %(asctime)s.%(msecs)03d %(levelname)s %(message)s
    2025-04-15 14:32:01.123 INFO LINE=1 TRACK=42 CLASS=Bottle DEFECT=0

analytics_routes.parse_log_line() relies on this format — do NOT change the
column order without updating the parser.
"""
import gzip
import logging
import logging.handlers
import multiprocessing
import os
import shutil
from typing import Optional

LOG_BASE_DIR = "logs"
for _sub in ("system", "objects", "error", "audit"):
    os.makedirs(os.path.join(LOG_BASE_DIR, _sub), exist_ok=True)

_LOG_FORMAT  = "%(asctime)s.%(msecs)03d %(levelname)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _GzipRotator:
    """Compress rotated log files in-place."""
    def __call__(self, source: str, dest: str) -> None:
        gz_dest = dest + ".gz"
        try:
            with open(source, "rb") as fi, gzip.open(gz_dest, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.remove(source)
        except Exception:
            pass


def _make_handler(category: str, subdir: str | None = None) -> logging.handlers.TimedRotatingFileHandler:
    if subdir:
        dirpath  = os.path.join(LOG_BASE_DIR, category, subdir)
        os.makedirs(dirpath, exist_ok=True)
        filepath = os.path.join(dirpath, f"{category}_{subdir}.log")
    else:
        filepath = os.path.join(LOG_BASE_DIR, category, f"{category}.log")

    h = logging.handlers.TimedRotatingFileHandler(
        filepath, when="midnight", interval=1, backupCount=90, encoding="utf-8"
    )
    h.rotator = _GzipRotator()
    h.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    return h


# ── Listener (main process only) ──────────────────────────────────────────

_log_queue = None
_listener  = None


# def start_log_listener(log_queue: multiprocessing.Queue | None = None) -> multiprocessing.Queue:
def start_log_listener(log_queue: Optional[multiprocessing.Queue] = None) -> multiprocessing.Queue:
    """
    Start the QueueListener in the main process.

    Always pass `log_queue` explicitly from main.py so workers receive
    the same queue object.  Calling without an argument creates an isolated
    queue that worker processes cannot write to.
    """
    global _listener, _log_queue

    if log_queue is None:
        log_queue = multiprocessing.Queue(maxsize=2_000_000)
    _log_queue = log_queue

    sys_handler   = _make_handler("system")
    obj_handler   = _make_handler("objects")
    error_handler = _make_handler("error")
    audit_handler = _make_handler("audit")
    line_handlers: dict[int, logging.Handler] = {}

    class _Router(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            name = record.name
            if name.startswith("ObjectTrackAI.objects.line_"):
                try:
                    lid = int(name.split("line_")[-1])
                except (ValueError, IndexError):
                    lid = None
                if lid is not None:
                    if lid not in line_handlers:
                        line_handlers[lid] = _make_handler("objects", f"line_{lid}")
                    try:
                        line_handlers[lid].emit(record)
                    except Exception:
                        error_handler.emit(record)
                try:
                    obj_handler.emit(record)
                except Exception:
                    error_handler.emit(record)
                return

            if name.startswith("ObjectTrackAI.system"):
                sys_handler.emit(record)
            elif name.startswith("ObjectTrackAI.audit"):
                audit_handler.emit(record)
            else:
                error_handler.emit(record)

    router = _Router()
    router.setLevel(logging.DEBUG)

    _listener = logging.handlers.QueueListener(_log_queue, router, respect_handler_level=True)
    _listener.start()

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(_log_queue))
    root.setLevel(logging.DEBUG)

    return _log_queue


def stop_log_listener() -> None:
    global _listener
    if _listener:
        _listener.stop()
        _listener = None


def configure_worker_logging(log_queue: multiprocessing.Queue) -> None:
    """Call once at the top of each worker process."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.DEBUG)


def get_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(f"ObjectTrackAI.{name}")
    lg.setLevel(logging.DEBUG)
    lg.propagate = True
    return lg


# ── Public helpers ────────────────────────────────────────────────────────

def log_system(msg: str) -> None:
    get_logger("system").info(msg)

# def log_error(msg: str, exc: BaseException | None = None) -> None:
def log_error(msg: str, exc: Optional[BaseException] = None) -> None:
    get_logger("error").error(msg, exc_info=exc)

def log_audit(user: str, action: str, ip: str = "localhost", **kwargs) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
    get_logger("audit").info(f"USER={user} ACTION={action} IP={ip} {extra}")

def log_app_start(version: str = "1.1.0") -> None:
    log_system(f"Application started | version={version}")

def log_app_stop() -> None:
    log_system("Application stopped | reason=graceful_shutdown")

def log_object_event(
    line_id: int, track_id: int, class_name: str, is_defective: bool = False
) -> None:
    """
    Structured object-counted event.
    Format consumed by analytics_routes.parse_log_line():
        LINE=<id> TRACK=<id> CLASS=<name> DEFECT=<0|1>
    """
    get_logger(f"objects.line_{line_id}").info(
        f"LINE={line_id} TRACK={track_id} CLASS={class_name} DEFECT={1 if is_defective else 0}"
    )
