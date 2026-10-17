"""Search -> evidence -> recommendation -> recorded decision -> staged download."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import uuid
import zipfile
from pathlib import Path

from .model_decisions import AnyJevEvaluator, JevEvaluator, OPTIONS, validate_result
from .model_graph import ModelGraph, canonical, fingerprint, timestamp
from .model_sources import ModelError, ModelHTTP, PolyHaven, Sketchfab, safe_path
from .model_reference import normalize_profile, checks as reference_checks, FIT

DEFAULT_POLICY = {"max_triangles": 100000, "max_materials": 8,
                  "allowed_licenses": ["cc0", "cc-by"], "min_confidence": 0.8,
                  "require_visual_review": True}


def text(value, name, maximum=2000, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ModelError(f"Invalid {name}.")
    return value.strip()


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ModelError(f"{name} must be an integer from {low} to {high}.")
    return value


class ModelCatalog:
    def __init__(self, database_path, *, providers=None, evaluators=None, cache_root=None):
        self.graph = ModelGraph(database_path)
        self.providers = providers or {"sketchfab": Sketchfab(), "polyhaven": PolyHaven()}
        from .model_oauth import SketchfabOAuth
        from .model_credentials import ProtectedOAuthStore
        self.oauth = SketchfabOAuth(store=ProtectedOAuthStore.for_catalog(database_path))
        if isinstance(self.providers.get("sketchfab"), Sketchfab):
            self.providers["sketchfab"].session_token = self.oauth.token
        self.evaluators = evaluators or {"jev": JevEvaluator(), "anyjev": AnyJevEvaluator()}
        self.cache_root = Path(cache_root or os.getenv("FAFNIR_MODEL_CACHE") or Path(database_path).parent / "model-downloads").resolve()

    def status(self):
        self.oauth.reload_saved_connection()
        return {"sources": list(self.providers), "sketchfab_authenticated": self.oauth.authenticated(),
                "sketchfab_oauth_configured": self.oauth.configured(),
                "sketchfab_connection": self.oauth.storage_status(),
                "evaluators": {"local": True, "jev": bool(os.getenv("TYPESAFE_API_KEY")),
                               "anyjev": bool(os.getenv("FAFNIR_ANYJEV_MODEL"))},
                "default_policy": DEFAULT_POLICY, "cache_root": str(self.cache_root),
                "graph": "SQLite property graph (nodes, indexed edges, bounded traversal)",
                "workflow": ["search", "detail", "evaluate", "decide", "prepare_download", "download"],
                "attribution": "Models provided by Sketchfab · Powered by Poly Haven",
                "dots": "Use FAFNIR's MCP tools through an authorized local connection/plugin. No separate Dots API is required."}

    def provider(self, model_id):
        source, separator, uid = model_id.partition(":")
        if not separator or source not in self.providers:
            raise ModelError("Unsupported model source.")
        return self.providers[source], uid

    def detail(self, model_id):
        provider, uid = self.provider(model_id)
        return self.graph.save_model(provider.detail(uid), detail=True)

    def search(self, p):
        query = text(p.get("query", ""), "query", 300)
        limit = integer(p.get("limit", 12), "limit", 1, 50)
        source = text(p.get("source", "all"), "source", 30)
        cursor = text(p.get("cursor", ""), "cursor", 500)
        if source not in {"all", "local", *self.providers}:
            raise ModelError("Unsupported model source.")
        if source == "all" and cursor:
            raise ModelError("Use one source when continuing a cursor.")
        if source == "local":
            try:
                offset = int(cursor or 0)
                if offset < 0:
                    raise ValueError()
            except ValueError:
                raise ModelError("Invalid local cursor.") from None
            found = self.graph.search(query, limit + 1, offset=offset)
            return {"models": found[:limit], "sources": {},
                    "next": {"local": str(offset + limit)} if len(found) > limit else {}}
        models, statuses, cursors = [], {}, {}
        for key in self.providers if source == "all" else [source]:
            try:
                found, next_cursor = self.providers[key].search(query, limit, cursor)
                models.extend(self.graph.save_model(v) for v in found)
                statuses[key] = {"status": "ok", "count": len(found)}
                if next_cursor:
                    cursors[key] = next_cursor
            except ModelError as exc:
                statuses[key] = {"status": "error", "message": str(exc)}
        return {"models": models, "sources": statuses, "next": cursors}

    def evaluate(self, p):
        model_id = text(p.get("model_id"), "model_id", 200, True)
        self.provider(model_id)
        model = self.graph.get(model_id)
        project = text(p.get("project", ""), "project", 500, True)
        brief = text(p.get("brief", ""), "brief", 6000, True)
        supplied = p.get("policy", {})
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_POLICY):
            raise ModelError("Unknown policy fields.")
        policy = {**DEFAULT_POLICY, **supplied}
        for key in ("max_triangles", "max_materials"):
            integer(policy[key], key, 0, 100000000)
        if type(policy["require_visual_review"]) is not bool:
            raise ModelError("require_visual_review must be boolean.")
        confidence = policy["min_confidence"]
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            raise ModelError("min_confidence must be between zero and one.")
        licenses = policy["allowed_licenses"]
        if not isinstance(licenses, list) or not licenses or len(licenses) > 30 or any(not isinstance(l, str) or len(l) > 100 for l in licenses):
            raise ModelError("allowed_licenses must be a nonempty list of license identifiers.")
        observations = p.get("observations", [])
        if not isinstance(observations, list) or len(observations) > 20:
            raise ModelError("At most twenty evidence observations are supported.")
        for entry in observations:
            if not isinstance(entry, dict) or set(entry) != {"kind", "result", "evidence"}:
                raise ModelError("Evidence needs kind, result, and evidence text.")
            if not isinstance(entry["kind"], str) or not isinstance(entry["result"], str) or entry["kind"] not in {"visual", "compatibility", "performance", "license"} or entry["result"] not in {"pass", "fail", "unknown"}:
                raise ModelError("Invalid evidence category/result.")
            text(entry["evidence"], "evidence", 3000, True)
        checks = []
        def check(name, state, evidence):
            checks.append({"name": name, "result": state, "evidence": evidence})
        check("license", "unknown" if model["license"] == "unknown" else "pass" if model["license"] in licenses else "fail", model["license"])
        check("downloadable", "pass" if model["downloadable"] else "fail", str(model["downloadable"]))
        for key, maximum in (("triangles", "max_triangles"), ("materials", "max_materials")):
            value = model.get(key)
            state = "unknown" if not isinstance(value, int) else "pass" if value <= policy[maximum] else "fail"
            check(key, state, f"{value} / limit {policy[maximum]} (provider metadata, not a Unity measurement)")
        for entry in observations:
            check(entry["kind"], entry["result"], entry["evidence"])
        if policy["require_visual_review"] and not any(e["kind"] == "visual" for e in observations):
            check("visual", "unknown", "No actual visual review was supplied.")
        provider = text(p.get("evaluator", "local"), "evaluator", 100)
        if provider not in {"local", *self.evaluators}:
            raise ModelError("Unknown evaluator.")
        profile = normalize_profile(p.get("reference_profile"))
        state = {"model": model, "project": project, "brief": brief, "policy": policy, "checks": list(checks), "reference_profile": profile}
        recommendation = None
        if provider != "local":
            # Only the bounded model/brief/evidence envelope crosses the provider boundary.
            try:
                recommendation = validate_result(self.evaluators[provider].evaluate(state))
            except ModelError:
                raise
            except Exception:
                raise ModelError("Decision provider failed. Check its configuration or use local checks.") from None
        judgments = (recommendation or {}).get("reference_judgments", {})
        if not isinstance(judgments, dict) or (profile and set(judgments)-{c["axis"] for c in profile["criteria"]}):
            raise ModelError("Invalid reference judgments.")
        for vote in judgments.values():
            validate_result(vote, FIT)
        checks.extend(reference_checks(profile, judgments, confidence,
                      (recommendation or {}).get("confidence_kind") != "uncalibrated_probability"))
        failed = any(c["result"] == "fail" for c in checks)
        unknown = any(c["result"] == "unknown" for c in checks)
        proposed = "reject" if failed else "hold"
        if (recommendation and not failed and not unknown and recommendation["confidence"] >= confidence
                and recommendation.get("confidence_kind") != "uncalibrated_probability"):
            proposed = recommendation["choice"]
        key = "evaluation:" + uuid.uuid4().hex
        record = {"id": key, "model_id": model["id"], "model_revision": model["revision"],
                  "project": project, "brief": brief, "policy": policy, "checks": checks, "reference_profile": profile,
                  "recommendation": recommendation, "proposed_decision": proposed,
                  "created_at": timestamp(), "input_hash": fingerprint(state),
                  "notice": "Recommendation only. Unknown fields are unresolved. Final decisions and downloads are separate actions."}
        self.graph.record(key, "evaluation", record, model["id"], project)
        if profile:
            with self.graph.lock, self.graph.connection:
                for source in profile["sources"]:
                    source_key = "reference:" + fingerprint(source)
                    self.graph.node(source_key, "reference", {**source, "label": source["source"]})
                    self.graph.edge(key, "COMPARED_WITH", source_key)
        return record

    def decide(self, p):
        evaluation = self.graph.get(text(p.get("evaluation_id"), "evaluation_id", 200, True))
        if not evaluation.get("model_revision") or not evaluation["id"].startswith("evaluation:"):
            raise ModelError("An evaluation is required before recording a decision.")
        current = self.graph.get(evaluation["model_id"])
        if current["revision"] != evaluation["model_revision"]:
            raise ModelError("Model evidence changed. Evaluate again before deciding.")
        decision = text(p.get("decision"), "decision", 20, True)
        if decision not in OPTIONS:
            raise ModelError("Choose adopt, hold, or reject.")
        record = {"id": "decision:" + uuid.uuid4().hex, "evaluation_id": evaluation["id"],
                  "model_id": current["id"], "project": evaluation["project"], "decision": decision,
                  "reason": text(p.get("reason", ""), "reason", 3000, True), "created_at": timestamp()}
        self.graph.record(record["id"], "decision", record, current["id"], evaluation["project"])
        with self.graph.lock, self.graph.connection:
            self.graph.edge(record["id"], "BASED_ON", evaluation["id"])
        return record

    def prepare_download(self, p):
        model = self.detail(text(p.get("model_id"), "model_id", 200, True))
        if not model["downloadable"]:
            raise ModelError("Provider does not offer this model for download.")
        if model["license"] == "unknown":
            raise ModelError("Resolve the model license before downloading.")
        format = text(p.get("format", "gltf"), "format", 20)
        if format not in {"gltf", "glb", "usdz", "fbx", "blend", "usd"}:
            raise ModelError("Unsupported download format.")
        if model["formats"] and format not in model["formats"]:
            raise ModelError("Selected format is not offered for this model.")
        resolution = text(p.get("resolution", "2k"), "resolution", 10)
        if resolution not in {"1k", "2k", "4k", "8k", "16k"}:
            raise ModelError("Unsupported texture resolution.")
        maximum = integer(p.get("max_bytes", 512 * 1024 * 1024), "max_bytes", 1, 4 * 1024 * 1024 * 1024)
        key = uuid.uuid4().hex
        plan = {"id": key, "state": "prepared", "model": model,
                "format": format, "resolution": resolution, "max_bytes": maximum,
                "destination": str(self.cache_root / key), "approval_nonce": secrets.token_urlsafe(24),
                "created_at": timestamp(), "effect": "Download files into a new cache directory; do not import, execute, or overwrite project assets."}
        self.graph.operation(key, plan)
        return plan

    def download(self, p):
        key = text(p.get("plan_id"), "plan_id", 100, True)
        nonce = text(p.get("approval_nonce"), "approval_nonce", 200, True)
        plan = self.graph.claim_operation(key, nonce, p.get("accepted_license"))
        if plan["state"] == "complete":
            return plan["receipt"]
        try:
            current = self.detail(plan["model"]["id"])
            if current["revision"] != plan["model"]["revision"]:
                raise ModelError("Source metadata changed. Review a new download plan.")
            provider, uid = self.provider(current["id"])
            files = provider.files(uid, plan["format"], plan["resolution"])
            if not files or len(files) > 500:
                raise ModelError("Invalid package file count.")
            paths = [safe_path(f["path"]) for f in files]
            if len(set(p.casefold() for p in paths)) != len(paths) or "attribution.json" in [p.casefold() for p in paths]:
                raise ModelError("Duplicate or reserved package filename.")
            expected = sum(int(f.get("size") or 0) for f in files)
            if expected > plan["max_bytes"]:
                raise ModelError("Download exceeds the reviewed size limit.")
            # Destination is server generated; callers cannot redirect it into a project.
            destination = self.cache_root / key
            if destination.exists() or str(destination) != plan["destination"]:
                raise ModelError("Download destination changed or already exists.")
            self.cache_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".partial-", dir=self.cache_root) as temporary:
                staging = Path(temporary)
                records, total = [], 0
                for file in files:
                    target = staging / safe_path(file["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest, md5, count = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
                    with provider.http.request(file["url"], provider.download_hosts) as response, target.open("xb") as output:
                        while chunk := response.read(128 * 1024):
                            total += len(chunk)
                            count += len(chunk)
                            if total > plan["max_bytes"]:
                                raise ModelError("Download exceeds the reviewed size limit.")
                            output.write(chunk)
                            digest.update(chunk)
                            md5.update(chunk)
                    if file.get("size") is not None and count != file["size"]:
                        raise ModelError("Incomplete download; prepare a new plan to retry.")
                    if file.get("md5") and md5.hexdigest() != file["md5"]:
                        raise ModelError("Provider checksum mismatch.")
                    records.append({"path": file["path"], "bytes": count, "sha256": digest.hexdigest()})
                    if target.suffix == ".zip":
                        with zipfile.ZipFile(target) as archive:
                            members = archive.infolist()
                            if len(members) > 20000 or sum(i.file_size for i in members) > plan["max_bytes"] * 10:
                                raise ModelError("Archive expansion exceeds inspection limits.")
                            for item in members:
                                safe_path(item.filename.rstrip("/"))
                            # Archive stays intact; no vendor scripts are executed/extracted.
                receipt = {"id": "download:" + key, "model_id": current["id"], "source_url": current["url"],
                    "title": current["title"], "author": current["author"], "license": current["license"],
                    "license_url": current["license_url"], "destination": str(destination), "files": records,
                    "created_at": timestamp(), "state": "complete", "imported": False}
                (staging / "attribution.json").write_text(canonical(receipt), encoding="utf-8")
                staging.rename(destination)
            self.graph.record(receipt["id"], "download", receipt, current["id"])
            plan.update(state="complete", receipt=receipt)
            self.graph.operation(key, plan)
            return receipt
        except Exception as exc:
            plan["state"] = "failed"
            plan["error"] = str(exc) if isinstance(exc, ModelError) else "Download failed; no complete package was published."
            self.graph.operation(key, plan)
            raise ModelError(plan["error"]) from None

    def dispatch(self, action, p):
        fields = {
            "oauth_start": {"return_to"}, "oauth_callback": {"code", "state"},
            "status": set(), "search": {"query", "source", "limit", "cursor"},
            "detail": {"model_id"}, "graph": {"model_id", "depth"},
            "evaluate": {"model_id", "project", "brief", "policy", "observations", "evaluator", "reference_profile"},
            "decide": {"evaluation_id", "decision", "reason"},
            "prepare_download": {"model_id", "format", "resolution", "max_bytes"},
            "download": {"plan_id", "approval_nonce", "accepted_license"}, "download_status": {"plan_id"},
        }
        if action not in fields or not isinstance(p, dict) or set(p) - fields[action]:
            raise ModelError("Unsupported model operation or fields.")
        if action == "status":
            return self.status()
        if action == "oauth_start":
            return self.oauth.start(p.get("return_to", "/models.html"))
        if action == "oauth_callback":
            return self.oauth.finish(text(p.get("code", ""), "code", 1000), text(p.get("state", ""), "state", 200))
        if action == "detail":
            return self.detail(text(p.get("model_id"), "model_id", 200, True))
        if action == "graph":
            return self.graph.neighborhood(text(p.get("model_id"), "model_id", 200, True), integer(p.get("depth", 1), "depth", 1, 3))
        if action == "download_status":
            plan = self.graph.operation(text(p.get("plan_id"), "plan_id", 100, True))
            return {k: v for k, v in plan.items() if k != "approval_nonce"}
        return getattr(self, action)(p)
