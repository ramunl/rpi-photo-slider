"""Typed, validated, atomically persisted face-framing settings.

The saved file ``data/face_settings.json`` is the single durable source for the
review UI and the player. Face detection is disabled by default and the
face-centered zoom increase defaults to 40% (a 1.4x fit). The zoom value must be
a finite number in the closed range 0-300 percent; invalid input is rejected
before anything is written, so a failed save never changes the saved settings.
The player only reads and applies these settings live; the review endpoint owns
atomic persistence.
"""

import json
import math
from pathlib import Path

from catalog import atomic_json

SETTINGS_FILE = "face_settings.json"
DEFAULTS = {"enabled": False, "zoom_percent": 40}
MIN_ZOOM = 0
MAX_ZOOM = 300


def settings_path(data):
    return Path(data) / SETTINGS_FILE


def validate(value):
    """Return a normalized ``{"enabled", "zoom_percent"}`` dict or raise ValueError."""
    if not isinstance(value, dict):
        raise ValueError("settings must be a JSON object")
    enabled = value.get("enabled", DEFAULTS["enabled"])
    zoom = value.get("zoom_percent", DEFAULTS["zoom_percent"])
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
        raise ValueError("zoom_percent must be a number")
    if not math.isfinite(zoom):
        raise ValueError("zoom_percent must be a finite number")
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise ValueError(f"zoom_percent must be between {MIN_ZOOM} and {MAX_ZOOM}")
    return {"enabled": enabled, "zoom_percent": zoom}


def load(data):
    """Return saved settings (with defaults for a missing file) or raise ValueError."""
    path = settings_path(data)
    if not path.exists():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings file is not valid JSON: {exc}") from exc
    return validate(raw)


def save(data, value):
    """Validate and atomically persist settings; invalid input leaves the file unchanged."""
    settings = validate(value)
    path = settings_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, settings)
    return settings
