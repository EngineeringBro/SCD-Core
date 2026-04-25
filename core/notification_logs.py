"""
Notification Logs — handles the notification_log_append action type.
Appends a new row to the appropriate markdown table in notifications/.
"""
from __future__ import annotations
import yaml
from pathlib import Path
from core.resolution_suggestion import Action

LOGS_CONFIG = Path("configs/notification_logs.yaml")


def _load_config() -> dict:
    return yaml.safe_load(LOGS_CONFIG.read_text(encoding="utf-8")).get("logs", {})


def append_row(action: Action) -> None:
    """
    Execute a notification_log_append action.
    action.payload must contain:
      - log_file: key from notification_logs.yaml
      - row: dict of column -> value
    """
    payload = action.payload
    log_key = payload.get("log_file")
    row_data = payload.get("row", {})

    if not log_key:
        raise ValueError("notification_log_append action missing 'log_file' in payload")

    config = _load_config()
    if log_key not in config:
        raise ValueError(f"Unknown log_file '{log_key}'. Known: {list(config)}")

    log_cfg = config[log_key]
    file_path = Path(log_cfg["file"])
    columns = log_cfg["columns"]

    # Build the markdown row in column order
    cells = [str(row_data.get(col, "")) for col in columns]
    md_row = "| " + " | ".join(cells) + " |"

    # Append to file (file must exist with header already)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(md_row + "\n")
