# services/reporting_service.py — ObjectTrackAI

import pandas as pd
from datetime import datetime
import os
from core.logger_setup import log_audit_export, log_error


class ReportingService:
    @staticmethod
    def generate_daily_report(stats_history: dict,
                               user: str = "system",
                               ip: str = "-") -> str:
        """
        Converts in-memory object count history into a CSV report.
        stats_history: {track_id: {"class_id": int, "class_name": str, ...}}
        """
        data = []
        for track_id, info in stats_history.items():
            data.append({
                "Track_ID":   track_id,
                "Class_ID":   info.get("class_id", 0),
                "Class_Name": info.get("class_name", "object"),
                "Line_ID":    info.get("line_id", "-"),
                "Timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        df       = pd.DataFrame(data)
        os.makedirs("data/exports", exist_ok=True)
        filename = (
            f"data/exports/object_report_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        try:
            df.to_csv(filename, index=False)
            log_audit_export(user=user, report_type="DAILY", ip=ip)
        except Exception as exc:
            log_error("ReportingService", "Failed to write daily CSV report",
                      exc=exc, meta={"filename": filename})
            raise
        return filename
