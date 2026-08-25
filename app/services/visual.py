from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.domain import Listing, VisualAssessment


def normalize_name(value: str | None) -> str:
    value = (value or "").strip().casefold()
    value = re.sub(r"[_\-]+", " ", value)
    return re.sub(r"\s+", " ", value)


_DEFAULT_PREMIUM = {
    "black": 88,
    "onyx black": 100,
}

# The list is deliberately conservative. Unknown models are handled by manual rules.
_DEFAULT_COLOR_GROUPS = {
    "black": "black",
    "onyx black": "black",
    "midnight": "black",
    "obsidian": "black",
    "charcoal": "dark",
    "graphite": "dark",
    "dark gray": "dark",
    "dark grey": "dark",
    "white": "white",
    "snow": "white",
    "silver": "white",
    "red": "red",
    "crimson": "red",
    "scarlet": "red",
    "blue": "blue",
    "navy": "blue",
    "green": "green",
    "emerald": "green",
    "purple": "purple",
    "violet": "purple",
    "gold": "gold",
    "golden": "gold",
}

_CLOSE_GROUPS = {
    frozenset(("black", "dark")),
    frozenset(("white", "gold")),
    frozenset(("blue", "purple")),
}


class VisualEngine:
    def __init__(self, rules_path: Path):
        self.rules_path = Path(rules_path)
        self.premium_backdrops: dict[str, int] = dict(_DEFAULT_PREMIUM)
        self.color_groups: dict[str, str] = dict(_DEFAULT_COLOR_GROUPS)
        self.combinations: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        if not self.rules_path.exists():
            self.rules_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_default()
        try:
            data = json.loads(self.rules_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for name, weight in data.get("premium_backdrops", {}).items():
            self.premium_backdrops[normalize_name(name)] = int(weight)
        for name, group in data.get("color_groups", {}).items():
            self.color_groups[normalize_name(name)] = normalize_name(group)
        self.combinations = {
            f"{normalize_name(item.get('model'))}|{normalize_name(item.get('backdrop'))}": item
            for item in data.get("combinations", [])
            if item.get("model") and item.get("backdrop")
        }

    def _write_default(self) -> None:
        payload = {
            "premium_backdrops": {"Black": 88, "Onyx Black": 100},
            "color_groups": {},
            "combinations": [
                {
                    "model": "Black",
                    "backdrop": "Onyx Black",
                    "score": 100,
                    "monochrome": True,
                    "note": "Сильная чёрная комбинация",
                },
                {
                    "model": "Black",
                    "backdrop": "Black",
                    "score": 96,
                    "monochrome": True,
                    "note": "Чистый monochrome",
                },
            ],
        }
        self.rules_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    def _find_group(self, name: str) -> str | None:
        norm = normalize_name(name)
        if norm in self.color_groups:
            return self.color_groups[norm]
        # Token matching is useful when a model is named e.g. "Midnight Rider".
        for token, group in self.color_groups.items():
            if token and token in norm:
                return group
        return None


    def _read_payload(self) -> dict[str, Any]:
        try:
            return json.loads(self.rules_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"premium_backdrops": {}, "color_groups": {}, "combinations": []}

    def add_premium_backdrop(self, name: str, weight: int = 90) -> None:
        payload = self._read_payload()
        payload.setdefault("premium_backdrops", {})[name.strip()] = max(0, min(100, int(weight)))
        self.rules_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        self.reload()

    def add_combination(self, model: str, backdrop: str, score: int, monochrome: bool, note: str = "") -> None:
        payload = self._read_payload()
        combos = payload.setdefault("combinations", [])
        normalized_model = normalize_name(model)
        normalized_backdrop = normalize_name(backdrop)
        combos[:] = [
            item for item in combos
            if not (normalize_name(item.get("model")) == normalized_model
                    and normalize_name(item.get("backdrop")) == normalized_backdrop)
        ]
        combos.append({
            "model": model.strip(), "backdrop": backdrop.strip(),
            "score": max(0, min(100, int(score))),
            "monochrome": bool(monochrome), "note": note.strip(),
        })
        self.rules_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        self.reload()

    def assess(self, listing: Listing) -> VisualAssessment:
        model = normalize_name(listing.model)
        backdrop = normalize_name(listing.backdrop)
        manual = self.combinations.get(f"{model}|{backdrop}")
        reasons: list[str] = []

        premium_weight = self.premium_backdrops.get(backdrop, 0)
        is_premium = premium_weight > 0
        if is_premium:
            reasons.append(f"Фон {listing.backdrop} находится в базе дорогих")

        if manual:
            score = max(0, min(100, int(manual.get("score", 0))))
            mono = bool(manual.get("monochrome", False))
            note = str(manual.get("note", "")).strip()
            if note:
                reasons.append(note)
            return VisualAssessment(
                score=score,
                is_monochrome=mono,
                is_premium_backdrop=is_premium,
                color_match="manual",
                reasons=reasons,
            )

        model_group = self._find_group(model)
        backdrop_group = self._find_group(backdrop)
        mono = False
        color_match = "unknown"
        score = 25

        if model_group and backdrop_group:
            if model_group == backdrop_group:
                mono = True
                color_match = "exact"
                score = 82
                reasons.append("Модель и фон относятся к одной цветовой группе")
            elif frozenset((model_group, backdrop_group)) in _CLOSE_GROUPS:
                color_match = "close"
                score = 67
                reasons.append("Цвета модели и фона близки")
            else:
                color_match = "contrast"
                score = 42
        elif backdrop in model or model in backdrop:
            mono = True
            color_match = "name-match"
            score = 78
            reasons.append("Название цвета модели совпадает с фоном")

        if is_premium:
            score = max(score, 55 + round(premium_weight * 0.35))
            if mono:
                score = min(100, score + 12)

        return VisualAssessment(
            score=max(0, min(100, score)),
            is_monochrome=mono,
            is_premium_backdrop=is_premium,
            color_match=color_match,
            reasons=reasons,
        )
