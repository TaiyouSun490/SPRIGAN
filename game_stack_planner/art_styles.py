"""Explicit art-style annotations and clearly labelled text-derived hints."""
from __future__ import annotations

from .models import Candidate
from .planning_context import _contains, _normalized

STYLES = {
    "realistic": ("リアル", "リアリスティック", "realistic", "photorealistic", "photoreal"),
    "stylized": ("スタイライズ", "stylized", "stylised"),
    "low_poly": ("ローポリ", "low poly", "low-poly", "lowpoly"),
    "pixel_art": ("ピクセルアート", "ドット絵", "pixel art", "pixel-art"),
    "anime": ("アニメ調", "アニメ風", "anime", "manga"),
    "hand_painted": ("手描き風", "hand painted", "hand-painted"),
    "cartoon": ("カートゥーン", "cartoon", "cartoony"),
}
LABELS = {"realistic": "リアル", "stylized": "スタイライズ", "low_poly": "ローポリ", "pixel_art": "ピクセルアート", "anime": "アニメ調", "hand_painted": "手描き風", "cartoon": "カートゥーン"}


def style_hints(text: str) -> tuple[str, ...]:
    normalized = _normalized(text)
    return tuple(key for key, terms in STYLES.items() if any(_contains(normalized, term) for term in terms))


def candidate_styles(candidate: Candidate) -> dict[str, list[str]]:
    raw = candidate.metadata.get("user_art_styles")
    user = [str(item) for item in raw if isinstance(item, str)] if isinstance(raw, list) else []
    hints = list(style_hints(" ".join((candidate.title, candidate.description, *candidate.tags))))
    return {"user": user, "text_hints": hints}


def query_terms(prompt: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term for key in style_hints(prompt) for term in STYLES[key]))
