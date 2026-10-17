"""Explicit, bounded online smoke check. Not run by pytest; no secrets printed."""
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_stack_planner.model_catalog import ModelCatalog

with tempfile.TemporaryDirectory(prefix="fafnir-model-smoke-") as directory:
    catalog = ModelCatalog(Path(directory) / "test.sqlite3")
    try:
        result = catalog.search({"query": "wooden table", "limit": 2})
        print(json.dumps({"search": result["sources"], "models": [m["id"] for m in result["models"]]}))
        flashlight = catalog.detail("sketchfab:c72a51c719d64bc591ac9a4e169cc321")
        print(json.dumps({"detail": flashlight["title"], "triangles": flashlight["triangles"], "license": flashlight["license"]}))
        plan = catalog.prepare_download({"model_id": "polyhaven:wooden_table_02", "format": "gltf", "resolution": "1k", "max_bytes": 10 * 1024 * 1024})
        receipt = catalog.download({"plan_id": plan["id"], "approval_nonce": plan["approval_nonce"], "accepted_license": "cc0"})
        print(json.dumps({"download": receipt["state"], "files": receipt["files"], "imported": receipt["imported"]}))
    finally:
        catalog.graph.close()
