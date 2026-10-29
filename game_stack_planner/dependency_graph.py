"""Extract bounded, attributable dependency evidence; never infer integrations."""
from __future__ import annotations

from collections import deque, defaultdict
import re
from typing import Any

from .models import Candidate, ProjectSnapshot
from .compatibility import assess_candidate_compatibility

DEFAULT_DEPENDENCY_DEPTH = 8


def version_status(required: str, resolved: str) -> str:
    """UPM package manifest dependencies specify minimum versions.

    Compare only plain stable semantic versions. Git, ranges, prereleases and
    malformed values require the Unity resolver or human review.
    """
    pattern = r"(\d+)\.(\d+)\.(\d+)"
    a, b = re.fullmatch(pattern, required), re.fullmatch(pattern, resolved)
    if not a or not b:
        return "unknown"
    return "satisfied" if tuple(map(int, b.groups())) >= tuple(map(int, a.groups())) else "below_minimum"


def _public_version(value: str) -> str:
    if not value:
        return ""
    return value if re.fullmatch(r"[0-9][a-zA-Z0-9.+_-]{0,99}", value) else "非数値版（詳細非表示）"


def add_dependency_evidence(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]],
                            candidates: dict[str, Candidate], project: ProjectSnapshot | None, platform: str, *,
                            compatibility_cache: dict[str, dict[str, Any]] | None = None,
                            max_nodes: int = 500, max_depth: int = DEFAULT_DEPENDENCY_DEPTH, max_edges: int = 10000) -> None:
    """Follow explicit product/UPM edges, bounded and cycle safe."""
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1
           for value in (max_nodes, max_depth, max_edges)):
        raise ValueError("Dependency analysis limits must be positive integers")
    candidates = {**candidates, **({c.id: c for c in project.packages} if project else {})}
    compatibility_cache = {} if compatibility_cache is None else compatibility_cache
    locked = project.package_lock if project else {}
    installed = {c.id for c in project.packages} if project else set()
    by_package = {c.external_id.casefold(): c.id for c in candidates.values()
                  if c.source in {"local", "openupm"}}
    if project:
        by_package.update({c.external_id.casefold(): c.id for c in project.packages})
    seed = [key for key, value in nodes.items() if value.get("kind") == "candidate"]
    queue = deque((key, 0) for key in seed)
    enqueued = set(seed)
    visited: set[str] = set()
    seen_edges: set[tuple[str, str, str, str]] = set()
    truncated = False
    dependency_edge_count = 0
    while queue and len(visited) < max_nodes and dependency_edge_count < max_edges:
        cid, depth = queue.popleft()
        if cid in visited or depth > max_depth:
            continue
        visited.add(cid)
        source = candidates.get(cid)
        records: list[tuple[str, str, str, str, str]] = []
        if source:
            details = source.metadata.get("asset_store_details")
            if isinstance(details, dict):
                product_deps = details.get("dependencies") if isinstance(details.get("dependencies"), list) else []
                raw_ids = details.get("dependency_ids") if isinstance(details.get("dependency_ids"), list) else []
                product_deps = list(product_deps) + [{"product_id": i} for i in raw_ids if isinstance(i, (str, int))]
                truncated |= len(product_deps) > 100
                for dep in product_deps[:100]:
                    if not isinstance(dep, dict):
                        continue
                    product_id = str(dep.get("product_id") or "")
                    if product_id.isdecimal() and len(product_id) <= 20:
                        records.append(("asset_store:" + product_id, str(dep.get("title") or ""), "", "asset_store_product_metadata", str(details.get("source_url") or "")[:1000]))
            validation = source.metadata.get("local_validation")
            inspection = validation.get("package_inspection") if isinstance(validation, dict) else None
            if isinstance(inspection, dict):
                manifests = inspection.get("package_manifests") if isinstance(inspection.get("package_manifests"), list) else []
                truncated |= len(manifests) > 20
                for manifest in manifests[:20]:
                    if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies"), dict):
                        continue
                    truncated |= len(manifest["dependencies"]) > 100
                    for name, required in list(manifest["dependencies"].items())[:100]:
                        name = str(name)
                        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}", name):
                            records.append((by_package.get(name.casefold(), "upm:" + name.casefold()), name, str(required)[:200], "inspected_package_manifest", ""))
        # A known registry candidate still represents the package in Unity's
        # lock. Its presence in the catalog must not hide transitive lock edges.
        package_name = (source.external_id if source and source.source in {"local", "openupm"}
                        else cid[4:] if cid.startswith("upm:") else "")
        lock_entry = locked.get(package_name, {})
        if isinstance(lock_entry, dict) and isinstance(lock_entry.get("dependencies"), dict):
            truncated |= len(lock_entry["dependencies"]) > 100
            for name, required in list(lock_entry["dependencies"].items())[:100]:
                name = str(name)
                if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,200}", name):
                    records.append((by_package.get(name.casefold(), "upm:" + name.casefold()), name, str(required)[:200], "unity_packages_lock", ""))
        if depth == max_depth and records:
            truncated = True
            continue
        for target, label, required, basis, url in records:
            if dependency_edge_count >= max_edges:
                truncated = True
                break
            signature = (cid, target, basis, required)
            if signature in seen_edges:
                continue
            seen_edges.add(signature)
            known = candidates.get(target)
            package_name = label if basis != "asset_store_product_metadata" else ""
            lock_entry = locked.get(package_name, {}) if package_name else {}
            resolved = str(lock_entry.get("version") or "") if isinstance(lock_entry, dict) else ""
            present = bool(resolved) if package_name else target in installed
            availability = ("installed_in_project" if present else
                            "owned_not_confirmed_installed" if known and known.ownership in {"owned", "installed"} else "unconfirmed")
            status = version_status(required, resolved) if required and resolved else "unknown"
            compat = None
            if known and known.source == "asset_store":
                if target not in compatibility_cache:
                    compatibility_cache[target] = assess_candidate_compatibility(known, project=project, platform=platform)
                compat = compatibility_cache[target]
            nodes.setdefault(target, {"id": target, "kind": "dependency", "label": label or (known.title if known else target)})
            edges.append({"from": cid, "to": target, "relation": "requires", "basis": basis,
                          "availability": availability, "required_version": _public_version(required), "resolved_version": _public_version(resolved),
                          "version_status": status, "dependency_compatibility": compat,
                          "source_url": url, "depth": depth + 1})
            dependency_edge_count += 1
            if (known or resolved) and target not in enqueued:
                enqueued.add(target)
                queue.append((target, depth + 1))
    # Truncation is visible rather than silently presented as complete.
    if queue or truncated:
        nodes["analysis:dependency_limit"] = {"id": "analysis:dependency_limit", "kind": "warning", "label": "依存関係の探索上限に到達"}

    direct: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for edge in edges:
        if edge.get("relation") == "requires" and edge.get("from") in seed:
            direct[edge["to"]][edge["from"]] = edge
    pair_count = 0
    for target, parents in direct.items():
        ids = sorted(parents)
        for index, first in enumerate(ids):
            for second in ids[index + 1:]:
                if pair_count >= 2000:
                    nodes["analysis:dependency_limit"] = {"id": "analysis:dependency_limit", "kind": "warning", "label": "依存関係の探索上限に到達"}
                    return
                edges.append({"from": first, "to": second, "relation": "shares_dependency", "dependency": target,
                              "basis": "explicit_dependency_metadata", "status": "integration_unverified"})
                pair_count += 1


def plan_dependency_review(graph: dict[str, Any], selected: set[str]) -> dict[str, Any]:
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        if edge.get("relation") == "requires":
            outgoing[edge["from"]].append(edge)
    result: list[dict[str, Any]] = []
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    truncated = any(n.get("id") == "analysis:dependency_limit" for n in graph.get("nodes", []))
    for root in sorted(selected):
        queue = deque([(root, (root,))])
        visited = {root}
        while queue:
            current, path = queue.popleft()
            if len(path) > DEFAULT_DEPENDENCY_DEPTH:
                truncated |= bool(outgoing.get(current))
                continue
            for edge in outgoing.get(current, []):
                target = edge["to"]
                entry = {**edge, "root": root, "path": [*path, target], "cycle": target in path,
                         "selected_in_plan": target in selected}
                result.append(entry)
                by_target[target].append(entry)
                if target not in visited:
                    visited.add(target)
                    queue.append((target, (*path, target)))
    shared = []
    for target, entries in by_target.items():
        roots = sorted({item["root"] for item in entries})
        if len(roots) > 1:
            shared.append({"dependency": target, "candidates": roots,
                           "required_versions": sorted({item["required_version"] for item in entries if item.get("required_version")}),
                           "status": "below_minimum" if any(item.get("version_status") == "below_minimum" for item in entries) else "unknown",
                           "note": "同じ依存を共有。相互動作の検証ではありません。"})
    return {"dependencies": result, "shared_dependencies": shared,
            "truncated": truncated,
            "integration_status": "unverified"}
