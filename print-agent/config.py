"""
Tiny file-based config store for the print agent. Keeps the one setting a shop
owner ever needs to choose: which installed printer is the default for receipts.
Stored outside the app's own folder (in the user's home directory) so it
survives the agent being rebuilt/replaced/updated.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".receipt-print-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get() -> dict:
    _ensure_dir()
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as err:
        print(f"Config file was unreadable, ignoring it: {err}")
        return {}


def update(partial: dict) -> dict:
    """Merge `partial` into the stored config and persist it.
    Pass a key with value None to explicitly clear it (still stored as null,
    which reads back the same as if the key were absent)."""
    current = get()
    merged = {**current, **partial}
    _ensure_dir()
    CONFIG_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
