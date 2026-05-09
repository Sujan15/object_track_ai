# api/analytics_routes.py
import gzip
import glob
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

analytics_router = APIRouter(prefix="/api/analytics", tags=["Analytics"])
LOG_BASE = "logs/objects"


def _open_log(path: str):
    """Open plain or gzip-rotated log file transparently."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


def parse_log_line(line: str) -> dict | None:
    """Parse a log line emitted by log_object_event().

    logger_setup format:
        %(asctime)s.%(msecs)03d %(levelname)s %(message)s
        2025-04-15 14:32:01.123 INFO LINE=1 TRACK=42 CLASS=Bottle DEFECT=0
                                ^--- parts[2] is the log level — must be skipped
    """
    parts = line.strip().split()
    # Need at least: date time level LINE=… CLASS=…
    if len(parts) < 5:
        return None
    timestamp_str = parts[0] + " " + parts[1]
    try:
        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None

    # parts[2] is the log level (INFO/ERROR/…); key=val starts at parts[3]
    data: dict[str, str] = {}
    for part in parts[3:]:
        if "=" in part:
            k, v = part.split("=", 1)
            data[k] = v

    if "LINE" not in data or "CLASS" not in data:
        return None

    try:
        line_id = int(data["LINE"])
    except ValueError:
        return None

    return {
        "timestamp": timestamp,
        "line": line_id,
        "class": data["CLASS"],
        "track": data.get("TRACK", "?"),
        "defect": data.get("DEFECT", "0") == "1",
    }


def _iter_log_events(days: int):
    """Yield parsed events from all object log files within the last `days` days."""
    cutoff = datetime.now() - timedelta(days=days)
    pattern = f"{LOG_BASE}/line_*/objects_line_*.log*"
    for logfile in sorted(glob.glob(pattern)):
        try:
            with _open_log(logfile) as fh:
                for raw in fh:
                    if raw.startswith("#"):
                        continue
                    evt = parse_log_line(raw)
                    if evt and evt["timestamp"] >= cutoff:
                        yield evt
        except Exception:
            pass  # corrupt / unreadable file — skip silently


@analytics_router.get("/class-distribution")
async def class_distribution(days: int = Query(7, ge=1, le=30)):
    counts: dict[str, int] = {}
    for evt in _iter_log_events(days):
        counts[evt["class"]] = counts.get(evt["class"], 0) + 1
    return {"period_days": days, "class_distribution": counts}


@analytics_router.get("/hourly")
async def hourly_trend(days: int = Query(7, ge=1, le=30)):
    hourly: dict[str, int] = {}
    for evt in _iter_log_events(days):
        hour_key = evt["timestamp"].strftime("%Y-%m-%d %H:00")
        hourly[hour_key] = hourly.get(hour_key, 0) + 1
    result = [{"hour": k, "count": v} for k, v in sorted(hourly.items())]
    return result


@analytics_router.get("/line-breakdown")
async def line_breakdown(days: int = Query(7, ge=1, le=30)):
    """Per-line totals and class distribution — consumed by the line-comparison chart."""
    lines: dict[int, dict] = {}
    for evt in _iter_log_events(days):
        lid = evt["line"]
        if lid not in lines:
            lines[lid] = {"line": lid, "total": 0, "defects": 0, "classes": {}}
        entry = lines[lid]
        entry["total"] += 1
        if evt["defect"]:
            entry["defects"] += 1
        entry["classes"][evt["class"]] = entry["classes"].get(evt["class"], 0) + 1

    result = sorted(lines.values(), key=lambda x: x["line"])
    return {"period_days": days, "lines": result}


@analytics_router.get("/recent-events")
async def recent_events(days: int = Query(1, ge=1, le=7), limit: int = Query(200, ge=1, le=1000)):
    """Last `limit` individual object events — populates the detailed log table."""
    events = list(_iter_log_events(days))
    # Most-recent first
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    trimmed = events[:limit]
    # Serialise timestamp
    for e in trimmed:
        e["timestamp"] = e["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
    return {"events": trimmed}
