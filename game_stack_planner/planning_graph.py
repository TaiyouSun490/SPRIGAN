"""Bounded evidence graph for retrieval and whole-stack planning.

Edges are evidence or labelled retrieval hypotheses, not verified integrations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from .models import Candidate, GameRequirement, ProjectSnapshot
from .planning_context import SCENES, compare_scene
from .dependency_graph import add_dependency_evidence
from .art_styles import LABELS, STYLES, candidate_styles, style_hints


@dataclass(frozen=True)
class CandidateFeatures:
    identity_tokens: set[str]
    description_tokens: set[str]
    styles: dict[str, list[str]]


class CandidateIndex:
    """Inverted index of the fields used by the existing relevance gate."""
    def __init__(self, candidates: list[Candidate], tokenize: Callable[[str], set[str]],
                 *, context: tuple[str, ...] = ()):
        self.by_id = {candidate.id: candidate for candidate in candidates}
        self.tokens: dict[str, set[str]] = defaultdict(set)
        self.categories: dict[str, set[str]] = defaultdict(set)
        self.features: dict[str, CandidateFeatures] = {}
        self.scene_matches: set[str] = set()
        for candidate in self.by_id.values():
            features = CandidateFeatures(tokenize(" ".join((candidate.title, *candidate.tags))),
                                         tokenize(candidate.description), candidate_styles(candidate))
            self.features[candidate.id] = features
            styles = features.styles["user"]
            expanded = [term for style in styles for key in (style_hints(style) or (style,)) for term in STYLES.get(key, (style,))]
            for token in features.identity_tokens | features.description_tokens | tokenize(" ".join(expanded)):
                self.tokens[token].add(candidate.id)
            # Context evidence can be Japanese-only and absent from Latin tokens.
            if compare_scene(context, "visual_assets", candidate)["matches"]:
                self.scene_matches.add(candidate.id)
            if candidate.source not in {"github", "openupm"}:
                for category in candidate.categories:
                    self.categories[category].add(candidate.id)
        self.tokenize = tokenize

    def retrieve(self, requirement: GameRequirement, rag_ids: set[str], context_tokens: set[str] | None = None) -> list[Candidate]:
        found = set(self.categories.get(requirement.key, ()))
        if requirement.key == "visual_assets":
            found.update(self.scene_matches)
        for token in self.tokenize(requirement.query) | (context_tokens or set()):
            found.update(self.tokens.get(token, ()))
        found.update(rag_ids)
        # Stable IDs make ties reproducible independently of index insertion order.
        return [self.by_id[key] for key in sorted(found) if key in self.by_id]


def build_graph(requirements: tuple[GameRequirement, ...],
                recommendations: dict[str, list[dict[str, Any]]],
                catalog: list[Candidate], project: ProjectSnapshot | None,
                platform: str, context: tuple[str, ...] = (), *,
                features: dict[str, CandidateFeatures] | None = None,
                compatibility_cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the bounded graph from shortlisted evidence and explicit dependencies."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    candidates = {candidate.id: candidate for candidate in catalog}
    platform_id = "platform:" + platform
    nodes[platform_id] = {"id": platform_id, "kind": "platform", "label": platform}
    for key in context:
        context_id = "context:" + key
        nodes[context_id] = {"id": context_id, "kind": "context", "label": SCENES[key]["label"], "basis": "prompt_keyword_hint"}
    for requirement in requirements:
        req_id = "requirement:" + requirement.key
        nodes[req_id] = {"id": req_id, "kind": "requirement", "label": requirement.title,
                         "priority": requirement.priority}
        if requirement.key == "visual_assets":
            for key in context:
                edges.append({"from": req_id, "to": "context:" + key, "relation": "contextualized_by", "basis": "prompt_keyword_hint"})
        for item in recommendations.get(requirement.key, []):
            candidate = item["candidate"]
            cid = str(candidate["id"])
            compat = candidate.get("compatibility") or {}
            nodes.setdefault(cid, {"id": cid, "kind": "candidate", "label": candidate["title"],
                                   "source": candidate["source"], "ownership": candidate["ownership"],
                                   "installed_in_project": bool(item.get("installed_in_project")),
                                   "compatibility": compat.get("status", "unknown"),
                                   "compatibility_evidence": compat})
            edges.append({"from": cid, "to": req_id, "relation": "may_support",
                          "basis": "retrieval_and_scoring", "score": item["score"],
                          "evidence": list(item.get("reasons") or [])[:6],
                          "risks": list(item.get("risks") or [])[:5]})
            original = candidates.get(cid)
            if original is not None:
                cached = (features or {}).get(cid)
                styles = cached.styles if cached is not None else candidate_styles(original)
                for basis, values in styles.items():
                    for style in values:
                        for canonical in (style_hints(style) or (style,)) if basis == "user" else (style,):
                            style_id = "art_style:" + canonical
                            nodes.setdefault(style_id, {"id": style_id, "kind": "art_style", "label": LABELS.get(canonical, canonical)})
                            edges.append({"from": cid, "to": style_id, "relation": "art_style", "basis": "user_annotation" if basis == "user" else "metadata_keyword_hint"})
                for key in compare_scene(context, requirement.key, original)["matches"]:
                    edges.append({"from": cid, "to": "context:" + key, "relation": "matches_context",
                                  "basis": "candidate_metadata_keyword_hint"})
    for cid, node in tuple(nodes.items()):
        if node["kind"] != "candidate":
            continue
        source = candidates.get(cid)
        if source is None:
            continue
        compat = node["compatibility"]
        edges.append({"from": cid, "to": platform_id, "relation": "compatibility",
                      "status": compat, "basis": "structured_metadata", "evidence": node.get("compatibility_evidence", {})})
    add_dependency_evidence(nodes, edges, candidates, project, platform, compatibility_cache=compatibility_cache)
    return {"schema": "sprigan.planning-evidence-graph.v1", "nodes": list(nodes.values()),
            "edges": edges, "limitations": ["Requirement matches are retrieval hypotheses, not verified capabilities.",
            "Missing dependency or compatibility metadata remains unknown; integration is not verified."]}
