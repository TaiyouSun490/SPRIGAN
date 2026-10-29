"""Small, inspectable context vocabulary; matches are retrieval hints, not proof."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .models import Candidate


SCENES = {
    "forest": {"label": "森", "prompt": ("森林", "森", "forest", "woodland"),
               "assets": ("forest", "woodland", "woods", "trees", "nature", "森林")},
    "mountain": {"label": "山", "prompt": ("山岳", "山林", "山", "mountain", "alpine"),
                 "assets": ("mountain", "alpine", "mountains", "山岳")},
    "urban": {"label": "都市", "prompt": ("都市", "街", "city", "urban"),
              "assets": ("city", "urban", "street", "metropolitan", "都市")},
    "underwater": {"label": "水中", "prompt": ("海底", "水中", "underwater", "subsea"),
                   "assets": ("underwater", "subsea", "submerged", "水中")},
    "interior": {"label": "屋内", "prompt": ("屋内", "室内", "interior", "indoor"),
                 "assets": ("interior", "indoor", "interiors", "屋内")},
}


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


@lru_cache(maxsize=512)
def _term_pattern(term: str) -> re.Pattern | None:
    # Cache vocabulary patterns, never mutable catalog data or query results.
    if re.fullmatch(r"[a-z0-9 _-]+", term):
        return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
    return None


def _contains(text: str, term: str) -> bool:
    term = _normalized(term)
    # Most vocabulary terms are absent. Avoid a regex scan of the full product
    # description in that common case, while retaining exact Latin boundaries.
    if term not in text:
        return False
    pattern = _term_pattern(term)
    return pattern is None or bool(pattern.search(text))


def scene_context(prompt: str) -> tuple[str, ...]:
    text = _normalized(prompt)
    return tuple(key for key, data in SCENES.items() if any(_contains(text, term) for term in data["prompt"]))


def retrieval_terms(context: tuple[str, ...], requirement: str) -> tuple[str, ...]:
    if requirement != "visual_assets":
        return ()
    return tuple(dict.fromkeys(term for key in context for term in SCENES[key]["assets"]))


def compare_scene(context: tuple[str, ...], requirement: str, candidate: Candidate) -> dict[str, object]:
    if requirement != "visual_assets" or not context:
        return {"matches": (), "contrasts": ()}
    text = _normalized(" ".join((candidate.title, candidate.description, *candidate.tags)))
    matches = tuple(key for key in context if any(_contains(text, term) for term in SCENES[key]["assets"]))
    audio_terms = ("audio", "sound", "soundtrack", "music", "sfx", "foley")
    visual_terms = ("model", "models", "mesh", "meshes", "vegetation", "foliage", "environment", "terrain", "biome")
    if matches and any(_contains(text, term) for term in audio_terms) and not (
        "visual_assets" in candidate.categories or any(_contains(text, term) for term in visual_terms)
    ):
        matches = ()
    # Only contrast when the description expressly names a different known scene.
    contrasts = () if matches else tuple(key for key, data in SCENES.items()
        if key not in context and any(_contains(text, term) for term in data["assets"]))
    return {"matches": matches, "contrasts": contrasts}
