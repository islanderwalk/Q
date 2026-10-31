"""結構化日誌：一行一個 JSON 寫到 stdout。Cloud Run 會把它收進 Cloud Logging（jsonPayload），
本機則由 docker logs 看到同樣格式。severity 欄位對應 Cloud Logging 的嚴重度。"""
import json
import sys
from datetime import datetime, timezone


def log(event: str, severity: str = "INFO", **fields):
    rec = {"severity": severity, "event": event, "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
    rec.update(fields)
    sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()
