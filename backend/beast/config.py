from __future__ import annotations

import json
import os
from typing import Any, Dict


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "beast_config.json")


def load_config(path: str | None = None) -> Dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
