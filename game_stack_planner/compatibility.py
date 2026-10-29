"""Structured compatibility checks kept separate from semantic retrieval."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .models import Candidate, ProjectSnapshot


def project_state_fingerprint(project: ProjectSnapshot) -> str:
    """Fingerprint relevant inputs; the digest contains no raw package references."""
    state = {"unity": project.unity_version, "pipeline": project.render_pipeline,
             "pipeline_evidence": project.render_pipeline_evidence, "input": project.input_backend,
             "packages": sorted((p.id, p.version or "") for p in project.packages), "lock": project.package_lock}
    return sha256(json.dumps(state, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")).hexdigest()


def _unity_key(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    numbers = re.findall(r"\d+", str(value))
    return tuple(int(item) for item in numbers[:4]) if len(numbers) >= 2 else None


def _stream(value: tuple[int, ...] | None) -> tuple[int, int] | None:
    return value[:2] if value is not None and len(value) >= 2 else None


def _project_pipeline(value: str) -> str | None:
    normalized = str(value or "").casefold()
    if normalized == "urp":
        return "urp"
    if normalized == "hdrp":
        return "hdrp"
    if normalized in {"built-in", "built_in"}:
        return "built_in"
    return None


def assess_candidate_compatibility(
    candidate: Candidate,
    *,
    project: ProjectSnapshot | None,
    platform: str,
) -> dict[str, Any]:
    """Return evidence-backed compatibility without inferring missing fields."""
    details = candidate.metadata.get("asset_store_details")
    details = details if isinstance(details, dict) else None
    result: dict[str, Any] = {
        "status": "not_evaluated" if project is None else "unknown",
        "project_unity_version": project.unity_version if project else None,
        "project_render_pipeline": project.render_pipeline if project else None,
        "target_platform": platform,
        "matched_unity_version": None,
        "source_url": str(details.get("source_url") or "") if details else "",
        "reasons": [],
        "local_validation_status": None,
        "local_warnings": [],
    }
    if candidate.source != "asset_store":
        result["status"] = "not_applicable"
        return result
    local_validation = candidate.metadata.get("local_validation")
    local_validation = (
        local_validation if isinstance(local_validation, dict) else None
    )
    if project is not None and local_validation is not None:
        local_project = local_validation.get("project")
        local_project = local_project if isinstance(local_project, dict) else {}
        try:
            fingerprint = sha256(
                str(Path(project.path).resolve()).encode("utf-8")
            ).hexdigest()[:24]
        except OSError:
            fingerprint = ""
        same_project = fingerprint and fingerprint == str(local_project.get("fingerprint") or "")
        same_state = (same_project and local_project.get("state_fingerprint") == project_state_fingerprint(project)
                      and local_project.get("platform") == platform)
        if same_project and not same_state:
            result["local_warnings"].append("過去の検査は現在のプロジェクト状態・対象環境と一致せず、再検査が必要")
        if same_state:
            local_status = str(
                local_validation.get("overall_status") or "unknown"
            )
            result["local_validation_status"] = local_status
            static = local_validation.get("static_checks")
            static = static if isinstance(static, dict) else {}
            compile_test = local_validation.get("compile_test")
            compile_test = compile_test if isinstance(compile_test, dict) else {}
            local_failures = [
                str(value) for value in static.get("failures", [])
            ]
            if compile_test.get("state") == "failed":
                local_failures.append("一時プロジェクトでのコンパイル検査に失敗")
            if local_status == "incompatible" or local_failures:
                result["status"] = "incompatible"
                result["reasons"].extend(local_failures or ["ローカル検査で非互換"])
                return result
            result["local_warnings"] = [
                str(value) for value in static.get("warnings", [])
            ][:20]
    if details is None:
        result["reasons"].append("Asset Storeの商品詳細が未同期")
        return result

    state = str(details.get("product_state") or "unknown").casefold()
    if state in {"deprecated", "disabled", "unpublished"}:
        result["status"] = "incompatible"
        result["reasons"].append(f"Asset Storeの商品状態: {state}")
        return result

    supported_platforms = {
        str(value).casefold() for value in details.get("platforms", [])
        if str(value).strip()
    }
    if supported_platforms and platform.casefold() not in supported_platforms \
            and "all" not in supported_platforms:
        result["status"] = "incompatible"
        result["reasons"].append(f"対象プラットフォーム{platform}に非対応")
        return result

    if project is None:
        result["reasons"].append("Unityプロジェクト未指定のため互換性未判定")
        return result
    project_key = _unity_key(project.unity_version)
    original_key = _unity_key(str(details.get("original_unity_version") or ""))
    if project_key is None:
        result["reasons"].append("プロジェクトのUnityバージョンを取得できない")
        return result
    if original_key is not None and project_key < original_key:
        result["status"] = "incompatible"
        result["reasons"].append(
            "プロジェクトのUnityが商品のOriginal Unity versionより古い"
        )
        return result

    if project.render_pipeline_evidence == "package_presence_only":
        result["reasons"].append("Render Pipelineは導入パッケージからの推定で、実際の有効設定は未確認")
        return result

    raw_matrix = details.get("render_pipeline_compatibility")
    matrix = raw_matrix if isinstance(raw_matrix, list) else []
    rows: list[tuple[tuple[int, ...], dict[str, Any]]] = []
    for raw in matrix:
        if not isinstance(raw, dict):
            continue
        key = _unity_key(str(raw.get("unity_version") or ""))
        if key is not None and _stream(key) == _stream(project_key):
            rows.append((key, raw))
    eligible = [item for item in rows if item[0] <= project_key]
    if not eligible:
        if rows:
            result["status"] = "incompatible"
            result["reasons"].append(
                "同じUnity系統の互換性表はあるが、プロジェクトより新しい版のみ"
            )
        else:
            result["reasons"].append(
                "このUnity系統に一致するRender Pipeline互換性表がない"
            )
        return result

    _, matched = max(eligible, key=lambda item: item[0])
    matched_version = str(matched.get("unity_version") or "")
    result["matched_unity_version"] = matched_version
    pipeline = _project_pipeline(project.render_pipeline)
    if pipeline is None:
        result["reasons"].append("カスタムRender Pipelineかどうかを判定できない")
        return result
    compatible = {
        str(value).casefold()
        for value in matched.get("compatible_pipelines", [])
        if str(value).strip()
    }
    if pipeline not in compatible:
        result["status"] = "incompatible"
        result["reasons"].append(
            f"{matched_version}では{project.render_pipeline}に非対応"
        )
        return result

    result["status"] = "compatible"
    result["reasons"].append(
        f"Asset Store互換性表: {matched_version} / {project.render_pipeline}"
    )
    if result["local_validation_status"] == "validated":
        result["reasons"].append("一時プロジェクトのコンパイル検査に合格")
    return result


__all__ = ["assess_candidate_compatibility"]
