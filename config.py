"""Configuração persistente do PC Monitor (JSON em %APPDATA%/PCMonitor)."""
import copy
import json
import os
import uuid

APP_NAME = "PCMonitor"


def data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def history_dir():
    path = os.path.join(data_dir(), "historico")
    os.makedirs(path, exist_ok=True)
    return path


def config_path():
    return os.path.join(data_dir(), "config.json")


DEFAULT_METRICS = ["cpu.load", "cpu.temp", "ram.pct", "nv0.load", "nv0.temp"]


def new_widget(name="Widget"):
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "visible": True,
        "metrics": list(DEFAULT_METRICS),
        "x": 40,
        "y": 40,
        "opacity": 0.88,
        "scale": 1.0,
        "layout": "vertical",   # "vertical" | "horizontal"
        "bars": True,
        "title": True,
        "locked": False,
        "click_through": False,
        "accent": "#4cc9f0",
    }


DEFAULT = {
    "interval_ms": 1000,
    "thresholds": {"temp": [75, 88], "pct": [80, 95]},
    "alerts": {"enabled": True, "temp_c": 90, "ram_pct": 95, "cooldown_s": 300},
    "logging": {"enabled": True, "interval_s": 5, "retention_days": 30},
    "start_with_windows": False,
    "widgets": [],
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    cfg = copy.deepcopy(DEFAULT)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            _merge(cfg, json.load(f))
    except (OSError, ValueError):
        pass
    widgets = []
    for w in cfg.get("widgets") or []:
        if isinstance(w, dict):
            full = new_widget(w.get("name", "Widget"))
            full.update(w)
            widgets.append(full)
    if not widgets:
        widgets = [new_widget("Principal")]
    cfg["widgets"] = widgets
    return cfg


def save(cfg):
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
