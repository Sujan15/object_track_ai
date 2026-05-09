# api/object_routes.py
from fastapi import APIRouter
import api.state as state   # import module, not value — avoids frozen-None bug

object_router = APIRouter(prefix="/api/objects", tags=["Object Status"])


@object_router.get("/live")
async def live_status():
    rd = state.get_result_dict()
    if rd is None:
        return {"lines": []}

    lines = []
    for line_id, data in rd.items():
        if not data or "stats" not in data:
            continue
        s = data["stats"]
        total = s.get("total", 0)
        defects = s.get("defects", 0)
        lines.append({
            "line": line_id,
            "total": total,
            "defects": defects,
            "defect_rate": round(defects / total * 100, 1) if total else 0.0,
            "classes": s.get("classes", {}),
        })

    return {"lines": lines}
