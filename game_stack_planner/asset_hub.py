"""Unified owned/CC0 asset library, Asset Server bridge and durable acquisition jobs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener

from .asset_hub_sources import AssetServer, GENERATORS, KINDS, STORES, asset_identity, bounded, canonical_url, cc0, provider_cards, public_url, store_url
from .model_graph import canonical, fingerprint, timestamp
from .model_sources import ModelError, ModelHTTP, NoRedirect, safe_path

MEDIA = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
FORMATS = MEDIA | {".zip", ".unitypackage", ".fbx", ".gltf", ".glb", ".blend", ".obj", ".mtl", ".usd", ".usdz", ".usdc", ".spz", ".ply", ".splat", ".hdr", ".exr", ".png", ".jpg", ".jpeg", ".tga", ".webp", ".sbsar", ".uasset", ".uproject", ".uplugin", ".json", ".txt", ".bin"}


def positive(value, name, low=1, high=100):
    if type(value) is not int or not low <= value <= high:
        raise ModelError(f"{name} must be {low}–{high}.")
    return value


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def inspect_archive(path, maximum):
    if not zipfile.is_zipfile(path):
        return
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 20000 or sum(v.file_size for v in members) > maximum * 10:
            raise ModelError("Archive expansion exceeds the inspection limit.")
        for entry in members:
            safe_path(entry.filename.rstrip("/"))
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ModelError("Archive contains a symbolic link.")
    # Leave archives intact. Import/extraction is a distinct, reviewed operation.


class AssetHub:
    def __init__(self, database, *, upstream=None, cache_root=None, legacy=None, http_open=None):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS hub_assets(id TEXT PRIMARY KEY, source TEXT NOT NULL,
            kind TEXT NOT NULL, basis TEXT NOT NULL, search_text TEXT NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS hub_asset_filter ON hub_assets(basis, source, kind, updated_at);
          CREATE TABLE IF NOT EXISTS hub_operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
            state TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS hub_roots(id TEXT PRIMARY KEY, path TEXT NOT NULL, source TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS hub_syncs(id TEXT PRIMARY KEY, source TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        self.upstream = upstream or AssetServer()
        self.cache_root = Path(cache_root or os.getenv("FAFNIR_ASSET_CACHE") or Path(database).parent / "asset-downloads").resolve()
        self.legacy = legacy
        self.http_open = http_open or build_opener(NoRedirect()).open
        self._providers = None
        from .asset_hub_credentials import HubCredentials
        self.credentials = HubCredentials(database)
        from .asset_hub_sync import OwnedLibrarySync
        self.library_sync = OwnedLibrarySync(self)

    def close(self):
        self.db.close()

    def status(self, p=None):
        with self.lock:
            counts = [dict(r) for r in self.db.execute("SELECT basis,kind,count(*) AS count FROM hub_assets GROUP BY basis,kind")]
            syncs = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM hub_syncs ORDER BY created_at DESC LIMIT 12")]
            roots = [dict(r) for r in self.db.execute("SELECT * FROM hub_roots ORDER BY id")]
            jobs = [self.public_operation(json.loads(r[0])) for r in self.db.execute("SELECT payload FROM hub_operations ORDER BY updated_at DESC LIMIT 20")]
        return {"schema": "fafnir.asset-hub.v1", "counts": counts, "roots": roots,
                "syncs": syncs, "library_syncs": self.library_sync.library_sync_status({})["runs"], "operations": jobs, "cache_root": str(self.cache_root),
                "asset_server": self.upstream.base, "providers": provider_cards(),
                "connections": self.credentials.status(),
                "default_rights": "owned_or_cc0", "automatic_purchase": False,
                "notes": ["Owned means user-declared/imported purchase evidence, not independent purchase verification.",
                          "Unknown licences and engine restrictions remain explicit. Generated files are never relabelled CC0.",
                          "Generation requires a prepared job and acceptance of provider billing. Timeout never automatically resubmits a paid request."]}

    def providers(self, p):
        if self._providers is None or p.get("refresh") is True:
            self._providers = self.upstream.providers()
        return {"public_sources": self._providers, "owned_and_generation": provider_cards()}

    def _save(self, asset):
        search = " ".join([asset["title"], asset.get("description", ""), asset.get("author", ""), " ".join(asset.get("tags", []))]).casefold()
        self.db.execute("INSERT INTO hub_assets VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source,kind=excluded.kind,basis=excluded.basis,search_text=excluded.search_text,payload=excluded.payload,updated_at=excluded.updated_at",
                        (asset["id"], asset["source"], asset["type"], asset["rights"]["basis"], search, canonical(asset), timestamp()))

    def asset(self, asset_id):
        bounded(asset_id, "asset ID", 500, True)
        with self.lock:
            row = self.db.execute("SELECT payload FROM hub_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ModelError("Asset not found; search or import it first.")
        return json.loads(row[0])

    def remote_asset(self, row):
        if not isinstance(row, dict) or not cc0(row):
            return None
        asset_id = bounded(row.get("id"), "asset ID", 500, True)
        source = asset_id.split(":", 1)[0]
        if not re.fullmatch(r"[a-z0-9_-]{1,40}:[A-Za-z0-9_./:-]{1,450}", asset_id):
            raise ModelError("Invalid remote asset ID.")
        kind = row.get("type", "other")
        asset = {"id": asset_id, "source": source, "title": bounded(row.get("title", asset_id), "title", 1000),
                 "type": kind if kind in KINDS else "other", "url": canonical_url(row["url"]),
                 "description": str(row.get("description", ""))[:8000], "author": str(row.get("author", ""))[:500],
                 "thumbnail": row.get("thumbnailUrl") or row.get("thumbnail"),
                 "license": {"name": "CC0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "attributionRequired": False},
                 "rights": {"basis": "cc0", "evidence_kind": "provider_metadata", "purchase_verified": False,
                            "engine_restriction": "none", "license_reviewed": True},
                 "tags": [str(v)[:100] for v in row.get("tags", [])[:100]],
                 "formats": row.get("formats", [])[:40], "resolutions": row.get("resolutions", [])[:30],
                 "downloadable": row.get("downloadable") is True, "local_files": [], "price": {"free": True}}
        with self.lock:
            old = self.db.execute("SELECT payload FROM hub_assets WHERE id=?", (asset_id,)).fetchone()
        if old:
            previous = json.loads(old[0])
            asset["local_files"] = previous.get("local_files", [])
            for key in ("source_files", "acquired_selection"):
                if key in previous:
                    asset[key] = previous[key]
            # Public search must never erase a previously imported owned record.
            if previous["rights"]["basis"] != "cc0":
                return previous
        return asset

    def search(self, p):
        query = bounded(p.get("query", ""), "query", 500)
        limit = positive(p.get("limit", 20), "limit", high=100)
        offset = positive(p.get("offset", 0), "offset", low=0, high=100000)
        kinds = p.get("types", [])
        sources = p.get("providers", [])
        if not isinstance(kinds, list) or any(v not in KINDS for v in kinds) or not isinstance(sources, list) or any(not isinstance(v, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", v) for v in sources):
            raise ModelError("Invalid type/source filters.")
        if type(p.get("online", True)) is not bool or type(p.get("downloadable_only", False)) is not bool:
            raise ModelError("Search switches must be boolean.")
        rights = p.get("rights", "owned_or_cc0")
        scopes = {"owned": ["owned"], "cc0": ["cc0"], "generated": ["generated"], "owned_or_cc0": ["owned", "cc0"], "all": ["owned", "cc0", "generated"]}
        if rights not in scopes:
            raise ModelError("Unknown rights filter.")
        results, reports = [], []
        if p.get("online", True) and rights in {"cc0", "owned_or_cc0", "all"}:
            try:
                response = self.upstream.search(query, kinds, sources, limit, offset)
                with self.lock, self.db:
                    for row in response.get("results", [])[:1000]:
                        item = self.remote_asset(row)
                        if item:
                            self._save(item)
                            results.append(item)
                reports = response.get("providers", [])
            except (ModelError, KeyError, TypeError, ValueError):
                reports = [{"provider": "asset_server", "status": "error", "message": "Public search unavailable; local library remains usable."}]
        clauses, args = ["basis IN (" + ",".join("?" for _ in scopes[rights]) + ")"], list(scopes[rights])
        local_kinds = list(dict.fromkeys([*kinds, *(["audio"] if any(v in {"sfx", "music", "voice"} for v in kinds) else [])]))
        for values, column in ((local_kinds, "kind"), (sources, "source")):
            if values:
                clauses.append(column + " IN (" + ",".join("?" for _ in values) + ")")
                args.extend(values)
        for term in query.casefold().split()[:20]:
            clauses.append("instr(search_text,?)>0")
            args.append(term)
        with self.lock:
            rows = self.db.execute("SELECT payload FROM hub_assets WHERE " + " AND ".join(clauses) + " ORDER BY CASE basis WHEN 'owned' THEN 0 WHEN 'generated' THEN 1 ELSE 2 END,updated_at DESC,id LIMIT ? OFFSET ?", [*args, limit + 1, offset]).fetchall()
        local = [json.loads(r[0]) for r in rows[:limit]]
        if self.legacy and rights in {"owned", "owned_or_cc0", "all"} and (not sources or "asset_store" in sources):
            local.extend(self.legacy(query, limit))
        merged = {v["id"]: v for v in local}
        merged.update({v["id"]: v for v in results})
        values = sorted(merged.values(), key=lambda v: {"owned": 0, "generated": 1, "cc0": 2}[v["rights"]["basis"]])
        if p.get("downloadable_only"):
            values = [v for v in values if v.get("downloadable")]
        values = values[:limit]
        return {"query": query, "results": values, "providers": reports, "rights": rights,
                "next_offset": offset + limit if len(rows) > limit or len(results) >= limit else None,
                "note": "Only CC0 public results are included. Free-only is not treated as a CC0 licence."}

    def register_root(self, p):
        source = p.get("source")
        if source not in STORES and source not in GENERATORS:
            raise ModelError("Choose a supported source for this folder.")
        path = Path(bounded(p.get("path"), "folder", 4000, True)).expanduser().resolve()
        if not path.is_dir() or path == Path(path.anchor) or path == Path.home():
            raise ModelError("Choose a specific existing asset folder, not a drive or home directory.")
        key = fingerprint([str(path), source])[:24]
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO hub_roots VALUES(?,?,?)", (key, str(path), source))
        return {"id": key, "path": str(path), "source": source}

    def local_file(self, root_id, relative):
        with self.lock:
            row = self.db.execute("SELECT path FROM hub_roots WHERE id=?", (root_id,)).fetchone()
        if row is None:
            raise ModelError("Register the asset folder before linking local files.")
        safe_path(relative)
        root = Path(row[0]).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in FORMATS:
            raise ModelError("Local asset path is unavailable or outside its registered folder.")
        return path

    def import_owned(self, p):
        source = p.get("source")
        if source not in STORES and source not in GENERATORS:
            raise ModelError("Unknown ownership source.")
        if p.get("owned_confirmation") is not True:
            raise ModelError("Explicit declaration of ownership of this import is required.")
        rows = p.get("items")
        if "csv" in p:
            data = bounded(p["csv"], "CSV", 900000)
            rows = list(csv.DictReader(io.StringIO(data.lstrip("\ufeff"))))
        if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
            raise ModelError("Import 1–5000 entries per batch.")
        normal = []
        allowed = {"title", "url", "type", "license", "license_url", "author", "description", "tags", "root_id", "file", "engine_restriction", "license_reviewed", "entitlement_note"}
        for row in rows:
            if not isinstance(row, dict) or set(row) - allowed:
                raise ModelError("Import contains unsupported fields; do not import account data or private download links.")
            title = bounded(row.get("title"), "title", 1000, True)
            url = store_url(source, row.get("url", ""))
            kind = row.get("type", "other")
            if kind not in KINDS:
                raise ModelError("Invalid asset type.")
            restriction = row.get("engine_restriction", "unknown") or "unknown"
            if restriction not in {"none", "unity", "unreal", "unknown"}:
                raise ModelError("Invalid engine restriction.")
            license_name = bounded(row.get("license", "unknown") or "unknown", "licence", 200)
            license_url = row.get("license_url", "")
            if license_url:
                license_url = canonical_url(license_url)
            tags = row.get("tags", [])
            if isinstance(tags, str):
                tags = [v.strip() for v in tags.split(",") if v.strip()]
            if not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(t, str) or len(t) > 100 for t in tags):
                raise ModelError("Invalid asset tags.")
            basis = "generated" if source in GENERATORS else "owned"
            key = asset_identity(source, url)
            local = []
            if row.get("file"):
                root_id = bounded(row.get("root_id", ""), "root ID", 100, True)
                path = self.local_file(root_id, bounded(row["file"], "relative file path", 2000, True))
                with self.lock:
                    root_source = self.db.execute("SELECT source FROM hub_roots WHERE id=?", (root_id,)).fetchone()[0]
                if root_source != source:
                    raise ModelError("The local folder belongs to a different source.")
                local = [{"root_id": root_id, "relative": row["file"], "size_bytes": path.stat().st_size}]
            item = {"id": key, "source": source, "title": title, "url": url, "type": kind,
                    "description": bounded(row.get("description", ""), "description", 8000),
                    "author": bounded(row.get("author", ""), "author", 500), "tags": tags,
                    "license": {"name": license_name, "url": license_url, "attributionRequired": None},
                    "rights": {"basis": basis, "evidence_kind": "user_declared_purchase_export", "purchase_verified": False,
                               "engine_restriction": restriction, "license_reviewed": row.get("license_reviewed") in (True, "true"),
                               "entitlement_note": bounded(row.get("entitlement_note", ""), "entitlement note", 2000)},
                    "local_files": local, "downloadable": bool(local), "formats": [Path(row["file"]).suffix.lstrip(".")] if local else []}
            normal.append(item)
        with self.lock, self.db:
            for item in normal:
                prior = self.db.execute("SELECT payload FROM hub_assets WHERE id=?", (item["id"],)).fetchone()
                if prior and not item["local_files"]:
                    item["local_files"] = json.loads(prior[0]).get("local_files", [])
                    item["downloadable"] = bool(item["local_files"])
                self._save(item)
            receipt = {"id": "sync:" + uuid.uuid4().hex, "source": source, "count": len(normal), "created_at": timestamp(), "evidence_kind": "user_declaration", "purchase_verified": False}
            self.db.execute("INSERT INTO hub_syncs VALUES(?,?,?,?)", (receipt["id"], source, canonical(receipt), receipt["created_at"]))
        return {**receipt, "assets": normal}

    def export(self, p):
        ids = p.get("asset_ids", [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
            raise ModelError("Select 1–500 assets for a project manifest.")
        target = p.get("target", "unity")
        if target not in {"unity", "unreal", "blender", "web", "other"}:
            raise ModelError("Unknown target engine.")
        items, issues = [], []
        for asset_id in ids:
            asset = self.asset(asset_id)
            rights = asset["rights"]
            if rights["engine_restriction"] not in {"none", target}:
                issues.append({"id": asset_id, "reason": "engine_restriction_or_unknown"})
            if not rights["license_reviewed"]:
                issues.append({"id": asset_id, "reason": "license_review_required"})
            files = []
            for entry in asset.get("local_files", []):
                path = self.resolve_file(entry)
                files.append({"path": str(path), "size_bytes": path.stat().st_size, "sha256": digest(path)})
            if not files:
                issues.append({"id": asset_id, "reason": "files_not_acquired"})
            items.append({**asset, "resolved_files": files})
        return {"schema": "fafnir.project-assets.v1", "target": target, "assets": items,
                "ready": not issues, "issues": issues, "imported": False,
                "note": "Portable import manifest only. Does not execute vendor code or modify an engine project."}

    def review_rights(self, p):
        asset = self.asset(p.get("asset_id"))
        if asset["rights"]["basis"] == "cc0":
            raise ModelError("CC0 source evidence is refreshed from its provider, not overridden locally.")
        name = bounded(p.get("license"), "licence", 200, True)
        note = bounded(p.get("entitlement_note"), "rights/contract evidence note", 2000, True)
        engine = p.get("engine_restriction")
        if name.lower() in {"unknown", "free", "不明"} or engine not in {"none", "unity", "unreal"}:
            raise ModelError("Record the actual licence and its engine restriction.")
        if p.get("review_confirmation") is not True:
            raise ModelError("Confirm that you reviewed these licence conditions.")
        url = canonical_url(p["license_url"]) if p.get("license_url") else ""
        asset["license"].update(name=name, url=url)
        asset["rights"].update(license_reviewed=True, engine_restriction=engine, entitlement_note=note,
                               review_basis="user_confirmed_terms", reviewed_at=timestamp())
        with self.lock, self.db:
            self._save(asset)
        return asset

    def resolve_file(self, entry):
        if "root_id" in entry:
            return self.local_file(entry["root_id"], entry["relative"])
        relative = safe_path(entry["relative"])
        path = (self.cache_root / relative).resolve()
        if not path.is_relative_to(self.cache_root) or not path.is_file():
            raise ModelError("Cached asset file is missing.")
        return path

    def _operation(self, key, payload=None):
        with self.lock, self.db:
            if payload is not None:
                self.db.execute("INSERT INTO hub_operations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at",
                                (key, payload["kind"], payload["state"], canonical(payload), timestamp()))
            row = self.db.execute("SELECT payload FROM hub_operations WHERE id=?", (key,)).fetchone()
        if not row:
            raise ModelError("Asset operation not found.")
        return json.loads(row[0])

    @staticmethod
    def public_operation(value):
        return {k: v for k, v in value.items() if k not in {"nonce", "private_outputs"}}

    def operation(self, p):
        return self.public_operation(self._operation(bounded(p.get("operation_id"), "operation ID", 100, True)))

    def claim(self, key, nonce, kind):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT payload FROM hub_operations WHERE id=?", (key,)).fetchone()
                if not row:
                    raise ModelError("Asset operation not found.")
                plan = json.loads(row[0])
                if plan["kind"] != kind or not isinstance(nonce, str) or not secrets.compare_digest(nonce.encode(), plan["nonce"].encode()):
                    raise ModelError("Operation authorization does not match the prepared request.")
                if plan["state"] == "complete":
                    self.db.commit()
                    return plan
                if plan["state"] != "prepared":
                    raise ModelError("Already attempted; inspect status. Do not automatically resubmit.")
                plan["state"] = "running"
                self.db.execute("UPDATE hub_operations SET state=?,payload=?,updated_at=? WHERE id=?", ("running", canonical(plan), timestamp(), key))
                self.db.commit()
                return plan
            except Exception:
                self.db.rollback()
                raise

    def prepare_download(self, p):
        asset = self.asset(p.get("asset_id"))
        if not asset.get("downloadable"):
            raise ModelError("This source requires its official download UI or a linked local file.")
        maximum = positive(p.get("max_bytes", 33554432), "download cap", high=2 * 1024**3)
        format = bounded(p.get("format", ""), "format", 30)
        if not format:
            format = next((v for v in ("glb", "gltf", "fbx", "obj", "hdr", "exr", "jpg", "png", "zip") if v in asset.get("formats", [])), "")
        resolution = bounded(p.get("resolution", "2k"), "resolution", 30)
        if asset["rights"]["basis"] != "cc0" and not asset.get("local_files"):
            raise ModelError("Owned assets must use a linked local file or the existing authenticated store bridge.")
        if asset["rights"]["basis"] == "cc0":
            detail, selection = self.upstream.detail(asset["id"], format, resolution)
            fresh = self.remote_asset(detail)
            if not fresh:
                raise ModelError("Current source metadata no longer establishes CC0.")
            with self.lock, self.db:
                self._save(fresh)
            asset = fresh
        else:
            selection = {"files": [{"filename": Path(v["relative"]).name, "size_bytes": self.resolve_file(v).stat().st_size} for v in asset["local_files"]]}
        key = uuid.uuid4().hex
        if type(p.get("extract", True)) is not bool:
            raise ModelError("extract must be boolean.")
        plan = {"id": key, "kind": "download", "state": "prepared", "asset_id": asset["id"], "extract": p.get("extract", True),
                "asset_revision": fingerprint(asset), "license": asset["license"], "selection": self.selection_metadata(selection),
                "format": format, "resolution": resolution, "max_bytes": maximum,
                "max_expanded_bytes": maximum * 10 if p.get("extract", True) else 0,
                "destination": str(self.cache_root / key), "created_at": timestamp(), "nonce": secrets.token_urlsafe(32),
                "effect": "Stage files in the asset cache with checksums and provenance. No engine import or vendor execution."}
        self._operation(key, plan)
        return {**self.public_operation(plan), "approval_nonce": plan["nonce"]}

    @staticmethod
    def selection_metadata(selection):
        # Signed CDN URLs are ephemeral and must not be copied into logs/catalogue/receipts.
        if isinstance(selection, list):
            return [AssetHub.selection_metadata(v) for v in selection[:200]]
        if isinstance(selection, dict):
            return {k: AssetHub.selection_metadata(v) for k, v in selection.items() if k.lower() not in {"url", "bundleurl", "downloadurl", "download_url", "headers"}}
        return selection

    def _space(self, maximum):
        self.cache_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.cache_root).free < maximum + 64 * 1024**2:
            raise ModelError("Insufficient cache disk space for the requested byte cap and reserve.")

    def fetch(self, url, path, maximum, *, headers=None, payload=None, trusted_host=None):
        """Bounded HTTPS download. Auth is permitted only to the one generator API host."""
        for _ in range(6):
            current, configured = urlsplit(url), urlsplit(self.upstream.base)
            if (current.scheme, current.netloc) == (configured.scheme, configured.netloc) and current.path.startswith("/v1/assets/"):
                # Explicitly configured self-hosted Asset Server origin, not an arbitrary CDN redirect.
                ModelHTTP.validate_url(url, {configured.hostname}, local=getattr(self.upstream, "local", False))
            else:
                public_url(url, resolve=True)
            host = urlsplit(url).hostname
            if headers and host != trusted_host:
                raise ModelError("Credentials may only be sent to the configured provider API.")
            request = Request(url, data=None if payload is None else canonical(payload).encode(), headers={"User-Agent": "FAFNIR-AssetHub/1.0", **(headers or {})})
            try:
                response = self.http_open(request, timeout=60)
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308) and not headers and payload is None:
                    url = urljoin(url, exc.headers.get("Location", ""))
                    continue
                raise ModelError(f"Asset provider HTTP {exc.code}; no credentials or response body logged.") from None
            except (OSError, URLError, TimeoutError):
                raise ModelError("Asset provider unavailable; inspect the saved operation before retrying.") from None
            with response, Path(path).open("xb") as stream:
                expected = response.headers.get("Content-Length")
                if expected and int(expected) > maximum:
                    raise ModelError("Asset exceeds the prepared download cap.")
                total = 0
                for chunk in iter(lambda: response.read(1024 * 256), b""):
                    total += len(chunk)
                    if total > maximum:
                        raise ModelError("Asset exceeds the prepared download cap.")
                    stream.write(chunk)
                if total == 0 or (expected and total != int(expected)):
                    raise ModelError("Asset download is empty or incomplete.")
            return total
        raise ModelError("Too many asset redirects.")

    def download(self, p):
        key = bounded(p.get("operation_id"), "operation ID", 100, True)
        plan = self._operation(key)
        if p.get("accepted_license") != plan.get("license", {}).get("name"):
            raise ModelError("Accept the exact licence in the prepared download.")
        asset = self.asset(plan["asset_id"])
        if fingerprint(asset) != plan["asset_revision"] and plan["state"] != "complete":
            raise ModelError("Asset evidence changed; prepare a fresh download.")
        plan = self.claim(key, p.get("approval_nonce"), "download")
        if plan["state"] == "complete":
            return self.public_operation(plan)
        try:
            self._space(plan["max_bytes"] * (11 if plan["extract"] else 1))
            destination = self.cache_root / key
            with tempfile.TemporaryDirectory(dir=self.cache_root, prefix="staging-") as temp:
                staging = Path(temp)
                files, total = [], 0
                if asset["rights"]["basis"] == "cc0":
                    path = staging / "asset.bin"
                    total = self.fetch(self.upstream.bundle(asset["id"], plan["format"], plan["resolution"]), path, plan["max_bytes"])
                    if zipfile.is_zipfile(path):
                        path = path.rename(staging / "asset.zip")
                    else:
                        extension = "." + plan["format"] if plan["format"] else ".bin"
                        if extension.lower() not in FORMATS:
                            extension = ".bin"
                        path = path.rename(staging / ("asset" + extension))
                    inspect_archive(path, plan["max_bytes"])
                    files = [path]
                else:
                    for index, entry in enumerate(asset["local_files"]):
                        source = self.resolve_file(entry)
                        total += source.stat().st_size
                        if total > plan["max_bytes"]:
                            raise ModelError("Local files exceed the prepared byte cap.")
                        path = staging / (str(index) + "-" + source.name)
                        shutil.copyfile(source, path)
                        inspect_archive(path, plan["max_bytes"])
                        files.append(path)
                if plan["extract"]:
                    extracted = []
                    for archive_path in files:
                        if not zipfile.is_zipfile(archive_path):
                            continue
                        with zipfile.ZipFile(archive_path) as archive:
                            names = set()
                            for entry in archive.infolist():
                                if entry.is_dir():
                                    continue
                                relative = safe_path(entry.filename)
                                # Data-only extraction; code, executables and unknown formats stay in the archive.
                                if Path(relative).suffix.lower() not in FORMATS and Path(relative).name.lower() not in {"license", "licence", "readme"}:
                                    continue
                                target = staging / "contents" / relative
                                lowered = str(target.relative_to(staging)).casefold()
                                if lowered in names or target.exists():
                                    raise ModelError("Archive contains duplicate filenames.")
                                names.add(lowered)
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with archive.open(entry) as source, target.open("xb") as output:
                                    shutil.copyfileobj(source, output, 256 * 1024)
                                extracted.append(target)
                    files.extend(extracted)
                records = [{"relative": key + "/" + v.relative_to(staging).as_posix(), "size_bytes": v.stat().st_size, "sha256": digest(v)} for v in files]
                receipt = {"asset_id": asset["id"], "source": asset["url"], "license": asset["license"], "rights": asset["rights"], "files": records, "created_at": timestamp(), "imported": False}
                (staging / "provenance.json").write_text(canonical(receipt), encoding="utf-8")
                staging.rename(destination)
            asset["local_files"] = records
            asset["source_files"] = plan["selection"]
            asset["acquired_selection"] = {"format": plan["format"], "resolution": plan["resolution"]}
            with self.lock, self.db:
                self._save(asset)
            plan.update(state="complete", receipt=receipt)
            self._operation(key, plan)
            return self.public_operation(plan)
        except Exception as exc:
            plan.update(state="failed", error=str(exc) if isinstance(exc, ModelError) else "Acquisition failed; no complete asset was published.")
            self._operation(key, plan)
            raise ModelError(plan["error"]) from None

    def reusable_files(self, asset_id, format="", resolution="2k"):
        asset = self.asset(asset_id)
        files = asset.get("local_files", [])
        previous = asset.get("acquired_selection", {})
        if asset["rights"]["basis"] == "cc0" and (previous.get("resolution") != resolution or format and previous.get("format") != format):
            return None
        if not files:
            return None
        try:
            for entry in files:
                path = self.resolve_file(entry)
                if path.stat().st_size != entry["size_bytes"] or entry.get("sha256") and digest(path) != entry["sha256"]:
                    return None
        except (ModelError, OSError):
            return None
        return {"state": "available", "asset_id": asset_id, "files": files, "reused": True, "copied": False, "imported": False}

    def dispatch(self, action, p):
        fields = {"status": set(), "providers": {"refresh"}, "search": {"query", "types", "providers", "rights", "online", "limit", "offset", "free_only", "downloadable_only"},
                  "asset": {"asset_id"}, "register_root": {"source", "path"},
                  "import_owned": {"source", "items", "csv", "owned_confirmation"},
                  "library_sync_status": {"run_id"},
                  "begin_library_sync": {"run_id", "source", "owned_confirmation"},
                  "apply_library_sync_page": {"run_id", "page_index", "items", "terminal", "coverage"},
                  "set_library_sync_state": {"run_id", "state", "error_code"},
                  "export": {"asset_ids", "target"}, "operation": {"operation_id"},
                  "review_rights": {"asset_id", "license", "license_url", "engine_restriction", "entitlement_note", "review_confirmation"},
                  "prepare_download": {"asset_id", "format", "resolution", "max_bytes", "extract"},
                  "download": {"operation_id", "approval_nonce", "accepted_license"},
                  "prepare_generation": {"provider", "type", "prompt", "options", "entitlement_note", "max_output_bytes"},
                  "generate": {"operation_id", "approval_nonce", "accept_provider_billing"},
                  "poll_generation": {"operation_id"}, "collect_generation": {"operation_id"}}
        if action not in fields or not isinstance(p, dict) or set(p) - fields[action]:
            raise ModelError("Unsupported asset hub operation or fields.")
        if action == "asset":
            return self.asset(p.get("asset_id"))
        if action in {"library_sync_status", "begin_library_sync", "apply_library_sync_page", "set_library_sync_state"}:
            return getattr(self.library_sync, action)(p)
        if action in {"prepare_generation", "generate", "poll_generation", "collect_generation"}:
            from .asset_hub_generators import GenerationCoordinator
            return getattr(GenerationCoordinator(self), action)(p)
        return getattr(self, action)(p)
