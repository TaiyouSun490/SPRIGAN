"""Persistent local property graph and append-only model review history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .model_sources import ModelError


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ModelGraph:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS model_nodes (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, properties TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS model_node_kind ON model_nodes(kind);
            CREATE TABLE IF NOT EXISTS model_edges (
                source TEXT NOT NULL REFERENCES model_nodes(id), relation TEXT NOT NULL,
                target TEXT NOT NULL REFERENCES model_nodes(id), properties TEXT NOT NULL,
                PRIMARY KEY(source, relation, target));
            CREATE INDEX IF NOT EXISTS model_edge_target ON model_edges(target, relation);
            CREATE TABLE IF NOT EXISTS model_operations (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    def close(self):
        self.connection.close()

    def node(self, key, kind, props):
        self.connection.execute("INSERT INTO model_nodes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET properties=excluded.properties, updated_at=excluded.updated_at",
                                (key, kind, canonical(props), timestamp()))

    def edge(self, source, relation, target, props=None):
        self.connection.execute("INSERT INTO model_edges VALUES(?,?,?,?) ON CONFLICT(source,relation,target) DO UPDATE SET properties=excluded.properties",
                                (source, relation, target, canonical(props or {})))

    def save_model(self, value, *, detail=False):
        with self.lock, self.connection:
            previous = self.get(value["id"], required=False)
            # Search snippets must not erase the richer detail used by an active review.
            if previous and previous.get("detail_fetched") and not detail and previous.get("dataset_revision") == value.get("dataset_revision"):
                return previous
            value = {**value, "detail_fetched": detail, "revision": fingerprint(value)}
            self.node(value["id"], "model", value)
            self.connection.execute("DELETE FROM model_edges WHERE source=? AND relation IN ('FROM_SOURCE','BY_AUTHOR','HAS_LICENSE','TAGGED','HAS_FORMAT')", (value["id"],))
            facts = [("source", value["source"], "FROM_SOURCE"),
                     ("author", value["source"] + ":" + value["author_id"], "BY_AUTHOR"),
                     ("license", value["license"], "HAS_LICENSE")]
            facts += [("tag", t.casefold(), "TAGGED") for t in value["tags"]]
            facts += [("format", f, "HAS_FORMAT") for f in value["formats"]]
            for kind, label, relation in facts:
                key = f"{kind}:{label}"
                self.node(key, kind, {"label": value["author"] if kind == "author" else label})
                self.edge(value["id"], relation, key)
            return value

    def get(self, key, required=True):
        with self.lock:
            row = self.connection.execute("SELECT properties FROM model_nodes WHERE id=?", (key,)).fetchone()
        if row is None and required:
            raise ModelError("Model or review was not found. Search or refresh it first.")
        return json.loads(row[0]) if row else None

    def search(self, query, limit, source="", offset=0):
        with self.lock:
            rows = self.connection.execute("SELECT properties FROM model_nodes WHERE kind='model' ORDER BY updated_at DESC").fetchall()
        terms = query.casefold().split()
        values = [json.loads(r[0]) for r in rows]
        return [v for v in values if (not source or v["source"] == source)
                and all(t in canonical([v["title"], v["tags"], v["description"]]).casefold() for t in terms)][offset:offset + limit]

    def neighborhood(self, key, depth=1, limit=150):
        self.get(key)
        visited, frontier, edges = {key}, {key}, {}
        truncated = False
        with self.lock:
            for _ in range(depth):
                upcoming = set()
                for node in sorted(frontier):
                    rows = self.connection.execute("SELECT * FROM model_edges WHERE source=? OR target=? ORDER BY source,relation,target LIMIT ?", (node, node, limit + 1)).fetchall()
                    for row in rows:
                        endpoints = {row["source"], row["target"]}
                        if len(visited | endpoints) > limit or len(edges) >= limit:
                            truncated = True
                            continue
                        edges[(row["source"], row["relation"], row["target"])] = {
                            "source": row["source"], "relation": row["relation"], "target": row["target"], "properties": json.loads(row["properties"])}
                        upcoming |= endpoints - visited
                        visited |= endpoints
                frontier = upcoming
            nodes = [dict(self.connection.execute("SELECT id,kind,properties FROM model_nodes WHERE id=?", (i,)).fetchone()) for i in sorted(visited)]
        for node in nodes:
            node["properties"] = json.loads(node["properties"])
        return {"nodes": nodes, "edges": list(edges.values()), "truncated": truncated}

    def record(self, key, kind, value, model_id, project=""):
        with self.lock, self.connection:
            self.node(key, kind, value)
            self.edge(key, "REVIEWS" if kind in {"evaluation", "decision"} else "DOWNLOAD_OF", model_id)
            if project:
                project_id = "project:" + fingerprint(project)
                self.node(project_id, "project", {"label": project})
                self.edge(key, "FOR_PROJECT", project_id)

    def operation(self, key, value=None):
        with self.lock, self.connection:
            if value is not None:
                self.connection.execute("INSERT INTO model_operations VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (key, "download", canonical(value)))
            row = self.connection.execute("SELECT payload FROM model_operations WHERE id=?", (key,)).fetchone()
        if not row:
            raise ModelError("Download plan not found.")
        return json.loads(row[0])

    def claim_operation(self, key, nonce, accepted_license):
        import secrets
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT payload FROM model_operations WHERE id=?", (key,)).fetchone()
                if not row:
                    raise ModelError("Download plan not found.")
                plan = json.loads(row[0])
                if not secrets.compare_digest(nonce.encode("utf-8"), plan["approval_nonce"].encode("utf-8")):
                    raise ModelError("Invalid download approval.")
                if plan["state"] == "complete":
                    self.connection.commit()
                    return plan
                if plan["state"] != "prepared":
                    raise ModelError("Plan already attempted; inspect status and prepare a fresh plan to retry.")
                if accepted_license != plan["model"]["license"]:
                    raise ModelError("Accept the exact license shown in the download plan.")
                plan["state"] = "running"
                self.connection.execute("UPDATE model_operations SET payload=? WHERE id=?", (canonical(plan), key))
                self.connection.commit()
                return plan
            except Exception:
                self.connection.rollback()
                raise
