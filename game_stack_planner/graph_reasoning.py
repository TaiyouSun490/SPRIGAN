"""Graph-grounded dependency costs and conservative alternative diversity."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

from .dependency_graph import DEFAULT_DEPENDENCY_DEPTH


def dependency_issues(graph: dict[str, Any], candidate_ids: set[str], *, version_cost: float, incompatible_cost: float) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    """Find only explicit, project-specific negative evidence; unknown is not negative."""
    outgoing = defaultdict(list)
    for edge in graph.get("edges", []):
        if edge.get("relation") == "requires":
            outgoing[edge.get("from")].append(edge)
    result = {}
    for root in sorted(candidate_ids):
        queue = deque([(root, (root,))])
        visited = {root}
        issues: dict[tuple[str, str], dict[str, Any]] = {}
        while queue:
            current, path = queue.popleft()
            if len(path) > DEFAULT_DEPENDENCY_DEPTH:
                continue
            for edge in outgoing[current]:
                target = edge.get("to")
                if not isinstance(target, str):
                    continue
                evidence = {"dependency": target, "path": [*path, target], "basis": edge.get("basis"),
                            "required_version": edge.get("required_version"), "resolved_version": edge.get("resolved_version")}
                if edge.get("version_status") == "below_minimum":
                    issues.setdefault((target, "below_minimum"), {**evidence, "kind": "below_minimum", "cost": version_cost})
                compat = edge.get("dependency_compatibility") or {}
                if compat.get("status") == "incompatible":
                    issues.setdefault((target, "dependency_incompatible"), {**evidence, "kind": "dependency_incompatible", "cost": incompatible_cost,
                                                                      "reasons": list(compat.get("reasons") or [])[:3]})
                if target not in visited:
                    visited.add(target)
                    queue.append((target, (*path, target)))
        result[root] = issues
    return result


def diversify_candidates(items: list[dict[str, Any]], limit: int, tokenize: Callable[[str], set[str]],
                         *, penalty: float = 8.0, per_scope: bool = False, score_field: str = "score") -> list[dict[str, Any]]:
    """MMR-inspired reordering of a single scope; never changes relevance scores.

    Similarity is a conservative title-token Jaccard proxy, not proof of
    duplication. The first item is always the top relevance candidate.
    """
    remaining = list(items)
    selected: list[dict[str, Any]] = []
    signatures: dict[str, set[str]] = {}
    for item in remaining:
        candidate = item["candidate"]
        signatures[str(candidate["id"])] = tokenize(str(candidate.get("title") or ""))
    while remaining and len(selected) < limit:
        def effective(item: dict[str, Any]) -> tuple[float, float, str]:
            signature = signatures[str(item["candidate"]["id"])]
            similarities = []
            for prev in selected:
                if per_scope and prev["candidate"].get("scope") != item["candidate"].get("scope"):
                    continue
                other = signatures[str(prev["candidate"]["id"])]
                union = signature | other
                similarities.append(len(signature & other) / len(union) if union else 0.0)
            similarity = max(similarities, default=0.0)
            # Penalize only strong overlap; no cross-scope preference.
            adjustment = penalty * similarity if similarity >= 0.65 else 0.0
            score = float(item.get(score_field, item["score"]))
            return (-(score - adjustment), -score, str(item["candidate"]["id"]))
        choice = min(remaining, key=effective)
        remaining.remove(choice)
        selected.append(choice)
    return selected
