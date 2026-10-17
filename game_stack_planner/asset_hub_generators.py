"""Official generation APIs with durable single-submission semantics and explicit billing consent."""
from __future__ import annotations
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import urlsplit, quote
import uuid

from .asset_hub_sources import GENERATORS, bounded, public_url
from .model_graph import canonical, timestamp
from .model_sources import ModelError


def request_spec(provider, kind, prompt, options):
    if not isinstance(options, dict):
        raise ModelError("Generator options must be a JSON object.")
    if provider == "tripo":
        if set(options) - {"model_version", "face_limit", "texture", "pbr", "quad", "seed", "negative_prompt"}:
            raise ModelError("Unsupported Tripo options.")
        return {"url": "https://api.tripo3d.ai/v2/openapi/task", "body": {"type": "text_to_model", "prompt": prompt, **options}, "host": "api.tripo3d.ai", "header": "Authorization", "prefix": "Bearer ", "binary": False}
    if provider == "marble":
        if set(options) - {"model", "display_name", "image_url", "video_url"} or options.get("image_url") and options.get("video_url"):
            raise ModelError("Choose text, one image URL or one video URL for Marble.")
        world_prompt = {"type": "text", "text_prompt": prompt}
        for key, kind_name in (("image_url", "image"), ("video_url", "video")):
            if options.get(key):
                world_prompt.update(type=kind_name)
                world_prompt[kind_name + "_prompt"] = {"source": "uri", "uri": public_url(options[key])}
        body = {"display_name": options.get("display_name", prompt[:80]), "world_prompt": world_prompt}
        if options.get("model"):
            body["model"] = bounded(options["model"], "Marble model", 100, True)
        return {"url": "https://api.worldlabs.ai/marble/v1/worlds:generate", "body": body, "host": "api.worldlabs.ai", "header": "WLT-Api-Key", "prefix": "", "binary": False}
    if provider == "elevenlabs":
        fields = {"sfx": {"duration_seconds", "prompt_influence", "loop", "model_id"},
                  "music": {"music_length_ms", "force_instrumental", "model_id", "seed"},
                  "voice": {"voice_id", "model_id", "voice_settings", "language_code"}}[kind]
        if set(options) - fields:
            raise ModelError("Unsupported ElevenLabs options.")
        if kind == "sfx" and "duration_seconds" in options:
            duration = options["duration_seconds"]
            if type(duration) not in (int, float) or not .5 <= duration <= 30:
                raise ModelError("Sound-effect length must be 0.5–30 seconds.")
        if kind == "music" and "music_length_ms" in options:
            duration = options["music_length_ms"]
            if type(duration) is not int or not 3000 <= duration <= 600000:
                raise ModelError("Music length must be 3000–600000 ms.")
        if kind == "voice":
            voice = bounded(options.get("voice_id", ""), "voice ID", 100, True)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", voice):
                raise ModelError("Invalid voice ID.")
            path = "/text-to-speech/" + voice
            body = {"text": prompt, **{k: v for k, v in options.items() if k != "voice_id"}}
        else:
            path = "/sound-generation" if kind == "sfx" else "/music"
            body = {"text" if kind == "sfx" else "prompt": prompt, **options}
        return {"url": "https://api.elevenlabs.io/v1" + path, "body": body, "host": "api.elevenlabs.io", "header": "xi-api-key", "prefix": "", "binary": True}
    raise ModelError("This provider has no verified first-party generation API; import your own exported file instead.")


class GenerationCoordinator:
    def __init__(self, hub):
        self.hub = hub

    def prepare_generation(self, p):
        provider, kind = p.get("provider"), p.get("type")
        if provider not in GENERATORS or kind not in GENERATORS[provider]["kinds"]:
            raise ModelError("Unsupported provider/output type.")
        prompt = bounded(p.get("prompt"), "prompt", 4100, True)
        options = p.get("options", {})
        spec = request_spec(provider, kind, prompt, options)
        note = bounded(p.get("entitlement_note", ""), "subscription/licence note", 2000, True)
        maximum = p.get("max_output_bytes", 33554432)
        if type(maximum) is not int or not 1 <= maximum <= 2 * 1024**3:
            raise ModelError("Choose an output byte cap up to 2 GiB.")
        key = uuid.uuid4().hex
        plan = {"id": key, "kind": "generation", "state": "prepared", "provider": provider, "type": kind,
                "prompt": prompt, "options": options, "entitlement_note": note, "max_output_bytes": maximum,
                "created_at": timestamp(), "nonce": secrets.token_urlsafe(32),
                "billing": {"estimate": None, "currency": None, "hard_provider_cost_cap": False,
                            "notice": "Provider subscription/API credits are charged separately. Check your provider dashboard. This byte cap is not a price cap."},
                "effect": "Send the displayed prompt/options to the official provider once. Save outputs to the asset cache; no engine import."}
        self.hub._operation(key, plan)
        return {**self.hub.public_operation(plan), "approval_nonce": plan["nonce"]}

    def headers(self, provider, spec):
        key = self.hub.credentials.key(provider)
        if not key:
            raise ModelError("Configure this provider in Connections before submitting a job.")
        return {spec["header"]: spec["prefix"] + key, "Content-Type": "application/json"}

    def generate(self, p):
        if p.get("accept_provider_billing") is not True:
            raise ModelError("Accept the displayed provider billing before starting this job.")
        key = bounded(p.get("operation_id"), "operation ID", 100, True)
        plan = self.hub._operation(key)
        if plan["kind"] != "generation":
            raise ModelError("Not a prepared generation request.")
        spec = request_spec(plan["provider"], plan["type"], plan["prompt"], plan["options"])
        headers = self.headers(plan["provider"], spec)
        self.hub._space(plan["max_output_bytes"])
        plan = self.hub.claim(key, p.get("approval_nonce"), "generation")
        if plan["state"] == "complete":
            return self.hub.public_operation(plan)
        try:
            if spec["binary"]:
                with tempfile.TemporaryDirectory(dir=self.hub.cache_root, prefix="generation-") as temporary:
                    path = Path(temporary) / "audio.mp3"
                    self.hub.fetch(spec["url"], path, plan["max_output_bytes"], headers=headers, payload=spec["body"], trusted_host=spec["host"])
                    self.publish(plan, [path], Path(temporary))
            else:
                value = self.hub.upstream.http.json(spec["url"], {spec["host"]}, headers=headers, payload=spec["body"])
                if plan["provider"] == "tripo":
                    if value.get("code") != 0:
                        raise ModelError("Tripo rejected the generation request; check the provider dashboard.")
                    task_id = value.get("data", {}).get("task_id")
                else:
                    task_id = value.get("operation_id") or value.get("name")
                if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", task_id):
                    raise ModelError("Provider did not return a usable task ID. Inspect your provider dashboard before retrying.")
                plan.update(state="running", remote_task_id=task_id)
                self.hub._operation(key, plan)
            return self.hub.public_operation(self.hub._operation(key))
        except Exception as exc:
            # A timeout may occur after the remote server accepted a billable job.
            plan.update(state="submission_unknown", error="The provider request did not complete locally. Check the provider dashboard; this job will not be resubmitted automatically.")
            self.hub._operation(key, plan)
            raise ModelError(plan["error"]) from None

    def remote(self, plan):
        provider = plan["provider"]
        spec = request_spec(provider, plan["type"], plan["prompt"], plan["options"])
        headers = self.headers(provider, spec)
        task = quote(plan["remote_task_id"], safe="")
        path = "https://api.tripo3d.ai/v2/openapi/task/" + task if provider == "tripo" else "https://api.worldlabs.ai/marble/v1/operations/" + task
        return self.hub.upstream.http.json(path, {spec["host"]}, headers=headers)

    def poll_generation(self, p):
        key = bounded(p.get("operation_id"), "operation ID", 100, True)
        plan = self.hub._operation(key)
        if plan["kind"] != "generation":
            raise ModelError("Not a generation job.")
        if plan["state"] not in {"running", "output_ready"} or "remote_task_id" not in plan:
            return self.hub.public_operation(plan)
        value = self.remote(plan)
        if plan["provider"] == "tripo":
            task = value.get("data", {})
            status = task.get("status")
            state = "output_ready" if status == "success" else "failed" if status in {"failed", "cancelled", "banned", "expired"} else "running"
            progress = task.get("progress")
        else:
            state = "failed" if value.get("error") else "output_ready" if value.get("done") is True else "running"
            progress = None
        plan.update(state=state, progress=progress)
        self.hub._operation(key, plan)
        return self.hub.public_operation(plan)

    def output_urls(self, value):
        found = []
        def visit(node, depth=0):
            if depth > 12 or len(found) >= 30:
                return
            if isinstance(node, str) and node.startswith("https://"):
                suffix = Path(urlsplit(node).path).suffix.lower()
                if suffix in {".glb", ".gltf", ".fbx", ".obj", ".spz", ".ply", ".splat", ".png", ".jpg", ".zip", ".mp3", ".wav"}:
                    found.append(node)
            elif isinstance(node, dict):
                for item in node.values():
                    visit(item, depth + 1)
            elif isinstance(node, list):
                for item in node[:100]:
                    visit(item, depth + 1)
        visit(value)
        return list(dict.fromkeys(found))

    def collect_generation(self, p):
        from .asset_hub import inspect_archive
        key = bounded(p.get("operation_id"), "operation ID", 100, True)
        plan = self.hub._operation(key)
        if plan["kind"] != "generation" or plan["state"] != "output_ready":
            raise ModelError("Poll the completed generation before collecting its outputs.")
        value = self.remote(plan)
        if plan["provider"] == "marble":
            world = (value.get("response") or {}).get("world_id")
            if world:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", world):
                    raise ModelError("Invalid Marble world ID.")
                spec = request_spec("marble", plan["type"], plan["prompt"], plan["options"])
                value = self.hub.upstream.http.json("https://api.worldlabs.ai/marble/v1/worlds/" + world, {spec["host"]}, headers=self.headers("marble", spec))
        urls = self.output_urls(value)
        if not urls:
            raise ModelError("No supported output file was returned; check the provider's task page.")
        self.hub._space(plan["max_output_bytes"])
        # Reuse the existing task; collection never starts generation again.
        with tempfile.TemporaryDirectory(dir=self.hub.cache_root, prefix="generation-") as temporary:
            total, paths = 0, []
            for index, url in enumerate(urls):
                path = Path(temporary) / (str(index) + Path(urlsplit(url).path).suffix.lower())
                total += self.hub.fetch(url, path, plan["max_output_bytes"] - total)
                inspect_archive(path, plan["max_output_bytes"])
                paths.append(path)
            self.publish(plan, paths, Path(temporary))
        return self.hub.public_operation(self.hub._operation(key))

    def publish(self, plan, paths, staging):
        from .asset_hub import digest
        key = plan["id"]
        records = [{"relative": key + "/" + v.name, "size_bytes": v.stat().st_size, "sha256": digest(v)} for v in paths]
        asset = {"id": plan["provider"] + ":generated-" + key, "source": plan["provider"],
                 "title": plan["prompt"][:120], "type": plan["type"], "description": plan["prompt"],
                 "url": GENERATORS[plan["provider"]]["url"], "author": "User-generated", "tags": [],
                 "license": {"name": "provider-plan-terms", "url": GENERATORS[plan["provider"]]["docs"], "attributionRequired": None},
                 "rights": {"basis": "generated", "purchase_verified": False, "evidence_kind": "official_api_job",
                            "engine_restriction": "unknown", "license_reviewed": False, "entitlement_note": plan["entitlement_note"]},
                 "local_files": records, "downloadable": True, "formats": list({v.suffix.lstrip(".") for v in paths})}
        (staging / "provenance.json").write_text(canonical({"asset": asset, "provider_task_id": plan.get("remote_task_id"), "created_at": timestamp()}), encoding="utf-8")
        staging.rename(self.hub.cache_root / key)
        with self.hub.lock, self.hub.db:
            self.hub._save(asset)
        plan.update(state="complete", asset_id=asset["id"], files=records)
        self.hub._operation(key, plan)
