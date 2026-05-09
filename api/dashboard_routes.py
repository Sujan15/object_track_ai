# api/dashboard_routes.py
from fastapi import APIRouter
from collections import defaultdict
import api.state as state   # import module, not value — avoids frozen-None bug

dashboard_router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@dashboard_router.get("/summary")
async def summary():
    rd = state.get_result_dict()
    if rd is None:
        return {"total": 0, "class_counts": {}, "defects": 0}

    total_objects = 0
    total_defects = 0
    class_counts: dict[str, int] = defaultdict(int)

    for line_data in rd.values():
        if not line_data or "stats" not in line_data:
            continue
        stats = line_data["stats"]
        total_objects += stats.get("total", 0)
        total_defects += stats.get("defects", 0)
        for cls, cnt in stats.get("classes", {}).items():
            class_counts[cls] += cnt

    return {
        "total": total_objects,
        "defects": total_defects,
        "class_counts": dict(class_counts),
    }
