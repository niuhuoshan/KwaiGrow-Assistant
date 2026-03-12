from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml


class SelectorLoader:
    @staticmethod
    def load(path: str) -> Dict[str, str]:
        selector_path = Path(path)
        if not selector_path.exists():
            raise FileNotFoundError(f"selectors file not found: {path}")
        data = yaml.safe_load(selector_path.read_text(encoding="utf-8")) or {}
        selectors = data.get("selectors") or {}
        return {str(k): str(v) for k, v in selectors.items()}
