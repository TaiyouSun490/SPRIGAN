"""Transactional, replay-safe receipts for browser-owned-library scans.

Store sessions and pagination handles stay in the browser. This module accepts only
bounded product metadata, never order records, account identifiers or download URLs.
"""
from __future__ import annotations

import hashlib
import json
import re

from .asset_hub_sources import KINDS, asset_identity, bounded, store_url
from .model_graph import canonical, timestamp
from .model_sources import ModelError

SOURCES = {"booth", "fab", "humble"}
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
GUID = re.compile(r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}\Z|[a-fA-F0-9]{32}\Z")


class OwnedLibrarySync:
    def __init__(self, hub):
        self.hub = hub
        with hub.lock, hub.db:
            hub.db.executescript("""
              CREATE TABLE IF NOT EXISTS hub_library_runs(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS hub_library_batches(
                run_id TEXT NOT NULL, page_index INTEGER NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(run_id,page_index));
              CREATE TABLE IF NOT EXISTS hub_library_seen(
                run_id TEXT NOT NULL, asset_id TEXT NOT NULL, PRIMARY KEY(run_id,asset_id));
            """)

    def _get(self, run_id):
        if not isinstance(run_id, str) or not TOKEN.fullmatch(run_id):
            raise ModelError("Invalid library sync identifier.")
        row = self.hub.db.execute("SELECT payload FROM hub_library_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ModelError("Library sync not found; start it from the browser extension.")
        return json.loads(row[0])

    def _put(self, run):
        run["updated_at"] = timestamp()
        self.hub.db.execute("INSERT OR REPLACE INTO hub_library_runs VALUES(?,?,?,?,?)",
                            (run["id"], run["source"], run["state"], canonical(run), run["updated_at"]))

    def library_sync_status(self, p):
        with self.hub.lock:
            if p.get("run_id"):
                return self._get(p["run_id"])
            return {"runs": [json.loads(r[0]) for r in self.hub.db.execute(
                "SELECT payload FROM hub_library_runs ORDER BY updated_at DESC LIMIT 30")]}

    def begin_library_sync(self, p):
        source, run_id = p.get("source"), p.get("run_id")
        if not isinstance(source, str) or source not in SOURCES or not isinstance(run_id, str) or not TOKEN.fullmatch(run_id):
            raise ModelError("Invalid library sync source or identifier.")
        if p.get("owned_confirmation") is not True:
            raise ModelError("Confirm that these are your own purchase libraries before starting.")
        with self.hub.lock, self.hub.db:
            row = self.hub.db.execute("SELECT payload FROM hub_library_runs WHERE id=?", (run_id,)).fetchone()
            if row:
                previous = json.loads(row[0])
                if previous["source"] != source:
                    raise ModelError("Library sync source changed.")
                return previous
            run = {"id": run_id, "source": source, "state": "running", "pages": 0, "count": 0,
                   "created_at": timestamp(), "evidence_kind": "browser_owned_library",
                   "purchase_verified": False, "complete": False, "error_code": ""}
            self._put(run)
            return run

    @staticmethod
    def _normalize(source, row):
        allowed = {"source_product_id", "title", "url", "type", "author", "license"}
        if not isinstance(row, dict) or set(row) - allowed:
            raise ModelError("Only product metadata is accepted; do not send account/order data.")
        product_id = bounded(row.get("source_product_id"), "source product ID", 200, True)
        valid = (source == "booth" and re.fullmatch(r"[0-9]{1,20}", product_id)
                 or source == "fab" and GUID.fullmatch(product_id)
                 or source == "humble" and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", product_id))
        if not valid:
            raise ModelError("Invalid source product ID; order keys are not product IDs.")
        url = store_url(source, row["url"]) if row.get("url") else ""
        if source == "booth":
            if url and asset_identity(source, url) != "booth:" + product_id:
                raise ModelError("BOOTH product ID and URL differ.")
            url = "https://booth.pm/items/" + product_id
        if source == "fab" and url and asset_identity(source, url).lower() != "fab:" + product_id.lower():
            raise ModelError("Fab product ID and URL differ.")
        key = asset_identity(source, url) if url and source != "humble" else source + ":" + product_id.lower()
        kind = row.get("type", "other")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ModelError("Invalid asset type.")
        return {"id": key, "source": source, "source_product_id": product_id, "title": bounded(row.get("title"), "title", 1000, True),
                "url": url, "type": kind, "author": bounded(row.get("author", ""), "author", 500),
                "description": "", "tags": [], "formats": [], "local_files": [], "downloadable": False,
                "license": {"name": bounded(row.get("license") or "unknown", "licence", 200), "url": "", "attributionRequired": None},
                "rights": {"basis": "owned", "evidence_kind": "browser_owned_library", "purchase_verified": False,
                           "engine_restriction": "unknown", "license_reviewed": False, "entitlement_note": ""}}

    def apply_library_sync_page(self, p):
        index, rows, terminal = p.get("page_index"), p.get("items"), p.get("terminal")
        if type(index) is not int or not 0 <= index < 100000 or type(terminal) is not bool:
            raise ModelError("Invalid library page sequence or terminal flag.")
        if not isinstance(rows, list) or len(rows) > 200:
            raise ModelError("Send at most 200 product metadata rows per library page.")
        coverage = p.get("coverage", "")
        if not isinstance(coverage, str) or coverage not in {"", "booth_library_and_gifts_end", "fab_acquired_cursor_end", "humble_all_orders_end"}:
            raise ModelError("Unknown terminal evidence.")
        body_digest = hashlib.sha256(canonical({"items": rows, "terminal": terminal, "coverage": coverage}).encode()).hexdigest()
        with self.hub.lock, self.hub.db:
            run = self._get(p.get("run_id"))
            existing = self.hub.db.execute("SELECT digest FROM hub_library_batches WHERE run_id=? AND page_index=?", (run["id"], index)).fetchone()
            if existing:
                if existing[0] != body_digest:
                    raise ModelError("A committed library page changed; restart the source scan.")
                return {**run, "replayed": True}
            if run["state"] != "running" or index != run["pages"]:
                raise ModelError("Library page is out of sequence or sync is not running.")
            required = {"booth": "booth_library_and_gifts_end", "fab": "fab_acquired_cursor_end", "humble": "humble_all_orders_end"}[run["source"]]
            if terminal and coverage != required:
                raise ModelError("Full scan completion requires source-specific terminal evidence.")
            items = [self._normalize(run["source"], row) for row in rows]
            for item in items:
                prior = self.hub.db.execute("SELECT payload FROM hub_assets WHERE id=?", (item["id"],)).fetchone()
                # Preserve the user's review, file bindings and curation across repeated scans.
                if prior:
                    previous = json.loads(prior[0])
                    for key in ("rights", "license", "local_files", "formats", "tags", "description", "type", "source_files", "acquired_selection"):
                        if key == "license" and not previous.get("rights", {}).get("license_reviewed") and previous.get("license", {}).get("name") in {"", "unknown", None}:
                            continue
                        if key in previous:
                            item[key] = previous[key]
                    item["downloadable"] = bool(item["local_files"])
                item["library_observation"] = {"run_id": run["id"], "seen_at": timestamp(), "evidence_kind": "browser_owned_library"}
                self.hub._save(item)
                self.hub.db.execute("INSERT OR IGNORE INTO hub_library_seen VALUES(?,?)", (run["id"], item["id"]))
            self.hub.db.execute("INSERT INTO hub_library_batches VALUES(?,?,?)", (run["id"], index, body_digest))
            run["pages"] += 1
            run["count"] = self.hub.db.execute("SELECT count(*) FROM hub_library_seen WHERE run_id=?", (run["id"],)).fetchone()[0]
            run["error_code"] = ""
            if terminal:
                run.update(state="complete", complete=True, coverage=coverage)
                receipt = {**run, "id": "library:" + run["id"], "created_at": timestamp()}
                self.hub.db.execute("INSERT INTO hub_syncs VALUES(?,?,?,?)", (receipt["id"], run["source"], canonical(receipt), receipt["created_at"]))
            self._put(run)
            return {**run, "replayed": False}

    def set_library_sync_state(self, p):
        state = p.get("state")
        error = p.get("error_code", "")
        if not isinstance(state, str) or not isinstance(error, str) or state not in {"running", "paused", "needs_login", "blocked", "cancelled"} or error not in {
            "", "login_required", "permission_required", "site_changed", "library_changed", "rate_limited", "network_error", "native_unavailable", "tab_closed", "page_limit", "account_changed"}:
            raise ModelError("Invalid library sync state or error code.")
        with self.hub.lock, self.hub.db:
            run = self._get(p.get("run_id"))
            if run["state"] in {"complete", "cancelled"}:
                raise ModelError("A finished library sync cannot be resumed.")
            run.update(state=state, error_code=error)
            self._put(run)
            return run
