from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from game_stack_planner.model_catalog import ModelCatalog
from game_stack_planner.model_decisions import JevEvaluator, validate_result
from game_stack_planner.model_sources import ModelError, ModelHTTP, PolyHaven, Sketchfab, safe_path


MODEL = {"id": "polyhaven:pipe", "source": "polyhaven", "external_id": "pipe", "title": "Old pipe",
         "url": "https://polyhaven.com/a/pipe", "description": "Photographed pipe",
         "author": "Artist", "author_id": "artist", "license": "cc0", "license_url": "https://polyhaven.com/license",
         "tags": ["rust", "industrial"], "thumbnail": None, "triangles": 100, "vertices": 60,
         "materials": 1, "downloadable": True, "formats": ["gltf", "glb"]}


class FakeHTTP:
    def __init__(self, value=None, data=b"model"):
        self.value = value
        self.data = data
        self.calls = []

    def json(self, url, hosts, **kwargs):
        self.calls.append((url, hosts, kwargs))
        return copy.deepcopy(self.value)

    def request(self, url, hosts, **kwargs):
        ModelHTTP.validate_url(url, hosts)
        self.calls.append((url, hosts, kwargs))
        return io.BytesIO(self.data)


class Provider:
    name = "polyhaven"
    download_hosts = {"dl.polyhaven.org"}

    def __init__(self):
        self.value = copy.deepcopy(MODEL)
        self.http = FakeHTTP()
        self.file_records = [{"path": "model.glb", "url": "https://dl.polyhaven.org/model.glb", "size": 5}]

    def search(self, query, limit, cursor):
        return [copy.deepcopy(self.value)], "next-page"

    def detail(self, uid):
        return copy.deepcopy(self.value)

    def files(self, uid, format, resolution):
        return self.file_records


class Evaluator:
    def evaluate(self, state):
        return {"provider": "fake", "model": "test", "choice": "adopt", "confidence": .95,
                "probabilities": {"adopt": .95, "hold": .04, "reject": .01}}


class ModelFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.provider = Provider()
        self.catalog = ModelCatalog(self.root / "catalog.sqlite3", providers={"polyhaven": self.provider},
                                    evaluators={"fake": Evaluator()}, cache_root=self.root / "downloads")

    def tearDown(self):
        self.catalog.graph.close()
        self.temp.cleanup()

    def evaluate(self, **extra):
        self.catalog.detail(MODEL["id"])
        return self.catalog.evaluate({"model_id": MODEL["id"], "project": "Film", "brief": "A rusty pipe for a close-up",
                "evaluator": "fake", "observations": [{"kind": "visual", "result": "pass", "evidence": "Inspected provider turntable; joints and rust match"}], **extra})

    def plan(self, **extra):
        return self.catalog.prepare_download({"model_id": MODEL["id"], "format": "glb", **extra})

    def execute(self, plan, **extra):
        return self.catalog.download({"plan_id": plan["id"], "approval_nonce": plan["approval_nonce"], "accepted_license": "cc0", **extra})

    def test_search_graph_traversal_and_history_survive_refresh(self):
        found = self.catalog.search({"query": "pipe"})
        self.assertEqual(found["models"][0]["id"], MODEL["id"])
        evaluation = self.evaluate()
        decision = self.catalog.decide({"evaluation_id": evaluation["id"], "decision": "adopt", "reason": "Checked close-up"})
        self.provider.value["tags"] = ["metal"]
        self.catalog.detail(MODEL["id"])
        graph = self.catalog.graph.neighborhood(MODEL["id"], 2)
        self.assertIn(decision["id"], {n["id"] for n in graph["nodes"]})
        self.assertNotIn("tag:rust", {n["id"] for n in graph["nodes"]})
        self.assertIn("tag:metal", {n["id"] for n in graph["nodes"]})
        with self.assertRaisesRegex(ModelError, "changed"):
            self.catalog.decide({"evaluation_id": evaluation["id"], "decision": "hold", "reason": "Old review"})

    def test_search_snippet_does_not_erase_details(self):
        detail = self.catalog.detail(MODEL["id"])
        self.provider.value["triangles"] = None
        found = self.catalog.search({"query": "pipe"})
        self.assertEqual(detail["revision"], found["models"][0]["revision"])

    def test_saved_candidates_paginate_without_disappearing(self):
        for n in range(5):
            self.catalog.graph.save_model({**MODEL, "id": f"polyhaven:pipe{n}", "external_id": f"pipe{n}"})
        collected, cursor = [], ""
        while True:
            result = self.catalog.search({"query": "pipe", "source": "local", "limit": 2, "cursor": cursor})
            collected.extend(m["id"] for m in result["models"])
            cursor = result["next"].get("local")
            if not cursor:
                break
        self.assertEqual(len(collected), 5)
        self.assertEqual(len(set(collected)), 5)
        for cursor in ["bad", "-1"]:
            with self.assertRaises(ModelError):
                self.catalog.search({"source": "local", "cursor": cursor})

    def test_partial_provider_failure_is_visible(self):
        broken = Provider()
        broken.search = lambda *a: (_ for _ in ()).throw(ModelError("HTTP 429"))
        self.catalog.providers["sketchfab"] = broken
        result = self.catalog.search({"query": "pipe"})
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["sources"]["sketchfab"]["status"], "error")

    def test_evidence_and_policy_override_confident_model(self):
        self.assertEqual(self.evaluate()["proposed_decision"], "adopt")
        self.assertEqual(self.evaluate(observations=[])["proposed_decision"], "hold")
        self.assertEqual(self.evaluate(policy={"max_triangles": 99})["proposed_decision"], "reject")
        self.assertEqual(self.evaluate(policy={"max_triangles": 100})["proposed_decision"], "adopt")
        self.provider.value["materials"] = None
        self.assertEqual(self.evaluate()["proposed_decision"], "hold")
        self.assertEqual(self.evaluate(evaluator="local")["proposed_decision"], "hold")

    def test_review_can_hold_or_reject_and_is_append_only(self):
        evaluation = self.evaluate()
        for state in ["hold", "reject", "adopt"]:
            self.catalog.decide({"evaluation_id": evaluation["id"], "decision": state, "reason": state})
        graph = self.catalog.graph.neighborhood(MODEL["id"])
        self.assertEqual(sum(n["kind"] == "decision" for n in graph["nodes"]), 3)

    def test_invalid_options_and_nan_do_not_run_model(self):
        for policy in [{"min_confidence": float("nan")}, {"max_triangles": True}, {"allowed_licenses": "cc0"}, {"something": 1}]:
            with self.subTest(policy=policy), self.assertRaises(ModelError):
                self.evaluate(policy=policy)
        for limit in [0, 51, True, "12"]:
            with self.assertRaises(ModelError):
                self.catalog.search({"query": "pipe", "limit": limit})
        with self.assertRaises(ModelError):
            self.catalog.dispatch("search", {"url": "http://localhost/secret"})

    def test_download_complete_retry_provenance_no_secret_urls(self):
        plan = self.plan()
        result = self.execute(plan)
        calls = len(self.provider.http.calls)
        self.assertEqual(result, self.execute(plan))
        self.assertEqual(len(self.provider.http.calls), calls)
        self.assertFalse(result["imported"])
        self.assertEqual(result["files"][0]["sha256"], hashlib.sha256(b"model").hexdigest())
        self.assertTrue((Path(result["destination"]) / "attribution.json").exists())
        self.assertNotIn("dl.polyhaven.org", json.dumps(result))
        self.assertEqual(self.provider.http.calls[0][2], {})

    def test_download_nonce_license_and_changed_evidence(self):
        plan = self.plan()
        with self.assertRaises(ModelError):
            self.execute(plan, approval_nonce="wrong")
        with self.assertRaises(ModelError):
            self.execute(plan, accepted_license="cc-by")
        self.provider.value["license"] = "cc-by"
        with self.assertRaisesRegex(ModelError, "changed"):
            self.execute(plan)
        self.assertEqual(self.catalog.graph.operation(plan["id"])["state"], "failed")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_oversize_incomplete_traversal_and_missing_format_cleanup(self):
        cases = [
            [{"path": "model.glb", "url": "https://dl.polyhaven.org/model.glb", "size": 99}],
            [{"path": "../escape", "url": "https://dl.polyhaven.org/model.glb", "size": 5}],
            [{"path": "model.glb", "url": "http://127.0.0.1/secret", "size": 5}],
        ]
        for files in cases:
            self.provider.file_records = files
            plan = self.plan(max_bytes=10)
            with self.assertRaises(ModelError):
                self.execute(plan)
            self.assertFalse(Path(plan["destination"]).exists())
        self.provider.file_records = [{"path": "model.glb", "url": "https://dl.polyhaven.org/model.glb"}]
        with self.assertRaises(ModelError):
            self.execute(self.plan(max_bytes=1))
        self.assertEqual(list((self.root / "downloads").glob(".partial-*")), [])
        with self.assertRaises(ModelError):
            self.plan(format="fbx")

    def test_zip_is_inspected_without_extraction(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("../escape.txt", "bad")
        self.provider.http.data = data.getvalue()
        self.provider.file_records = [{"path": "model.zip", "url": "https://dl.polyhaven.org/model.zip", "size": len(data.getvalue())}]
        with self.assertRaises(ModelError):
            self.execute(self.plan())
        self.assertFalse((self.root / "escape.txt").exists())

    def test_graph_and_decision_survive_reopen(self):
        e = self.evaluate()
        self.catalog.graph.close()
        from game_stack_planner.model_graph import ModelGraph
        self.catalog.graph = ModelGraph(self.root / "catalog.sqlite3")
        self.assertEqual(self.catalog.graph.get(e["id"])["input_hash"], e["input_hash"])


class ProviderTests(unittest.TestCase):
    def test_sketchfab_real_shape_and_cursor(self):
        uid = "a" * 32
        http = FakeHTTP({"results": [{"uid": uid, "name": "Pipe", "isDownloadable": True,
             "license": {"slug": "by", "url": "https://creativecommons.org/licenses/by/4.0/"},
             "user": {"username": "artist"}, "faceCount": 24,
             "archives": {"gltf": {}}, "tags": [{"slug": "metal"}]}],
             "next": "https://api.sketchfab.com/v3/search?cursor=abc"})
        values, cursor = Sketchfab(http).search("old pipe", 12)
        self.assertEqual(values[0]["id"], "sketchfab:" + uid)
        self.assertEqual(values[0]["license"], "cc-by")
        self.assertEqual(cursor, "abc")
        self.assertIn("q=old+pipe", http.calls[0][0])

    def test_authentication_required_without_reading_browser_credentials(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ModelError, "sign-in"):
            Sketchfab(FakeHTTP()).files("a" * 32, "gltf", "2k")

    def test_polyhaven_dependencies_keep_relative_paths(self):
        http = FakeHTTP({"gltf": {"2k": {"gltf": {"url": "https://dl.polyhaven.org/chair.gltf", "size": 20,
                  "include": {"textures/chair.jpg": {"url": "https://dl.polyhaven.org/chair.jpg", "size": 15}}}}}})
        records = PolyHaven(http).files("chair", "gltf", "2k")
        self.assertEqual([r["path"] for r in records], ["chair.gltf", "textures/chair.jpg"])

    def test_jev_official_endpoint_and_contract(self):
        http = FakeHTTP({"model": "jev-1.13.0", "answers": {"adoption": {"type": "choice", "choice": "hold",
                         "confidence": .8, "probabilities": {"adopt": .1, "hold": .85, "reject": .05}}}})
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-only"}):
            result = JevEvaluator(http).evaluate({"brief": "Old iron bed"})
        self.assertEqual(result["choice"], "hold")
        self.assertEqual(http.calls[0][0], "https://api.typesafe.ai/v1/systemone")
        self.assertNotIn("test-only", json.dumps(result))
        for values in [{"adopt": 1, "hold": 1, "reject": 1}, {"adopt": float("nan"), "hold": 0, "reject": 0}]:
            with self.assertRaises(ModelError):
                validate_result({"choice": "adopt", "confidence": .9, "probabilities": values})

    def test_host_and_filename_boundaries(self):
        for path in ["../x", "C:/x", "\\host\\file", "a/../x", "NUL.txt", "a/x."]:
            with self.assertRaises(ModelError):
                safe_path(path)
        for url in ["https://api.sketchfab.com.evil.test/a", "http://api.sketchfab.com/a", "https://user:secret@api.sketchfab.com/a"]:
            with self.assertRaises(ModelError):
                ModelHTTP.validate_url(url, {"api.sketchfab.com"})


if __name__ == "__main__":
    unittest.main()
