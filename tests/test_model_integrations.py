"""Feature boundary tests; no network, user tokens, or model downloads."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from mcp.client import Client

from game_stack_planner.api import GameStackApplication
from game_stack_planner.cli import main
from game_stack_planner.mcp_server import build_mcp_server
from game_stack_planner.model_catalog import ModelCatalog
from game_stack_planner.model_decisions import JevEvaluator, validate_result
from game_stack_planner.model_graph import ModelGraph
from game_stack_planner.model_oauth import SketchfabOAuth
from game_stack_planner.model_sources import ModelError
from game_stack_planner.server import create_server
from test_model_discovery import Evaluator, FakeHTTP, MODEL, Provider


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "FAFNIR_SKETCHFAB_CLIENT_ID": "test-client",
            "FAFNIR_SKETCHFAB_CLIENT_SECRET": "test-secret",
            "FAFNIR_SKETCHFAB_REDIRECT_URI": "http://127.0.0.1:8770/api/models/oauth_callback",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = FakeHTTP({"access_token": "test-token", "refresh_token": "test-refresh", "expires_in": 60})
        self.oauth = SketchfabOAuth(self.http)

    def state(self):
        url = self.oauth.start()["authorize_url"]
        self.assertNotIn("test-secret", url)
        self.assertTrue(url.startswith("https://sketchfab.com/oauth2/authorize/?"))
        return parse_qs(urlsplit(url).query)["state"][0]

    def test_state_once_exchange_and_refresh_without_leaking_tokens(self):
        state = self.state()
        with self.assertRaises(ModelError):
            self.oauth.finish("code", "wrong-state")
        result = self.oauth.finish("code", state)
        self.assertEqual(result, {"authenticated": True})
        self.assertEqual(self.oauth.token(), "test-token")
        self.assertEqual(self.http.calls[0][2]["form"]["grant_type"], "authorization_code")
        with self.assertRaises(ModelError):
            self.oauth.finish("code", state)
        self.oauth.expires_at = 0
        self.http.value["access_token"] = "refreshed-test-token"
        self.assertEqual(self.oauth.token(), "refreshed-test-token")
        self.assertEqual(self.http.calls[-1][2]["form"]["grant_type"], "refresh_token")

    def test_expiry_redirect_and_invalid_response(self):
        state = self.state()
        self.oauth.pending[state] = 0
        with self.assertRaisesRegex(ModelError, "expired"):
            self.oauth.finish("code", state)
        for redirect in ["https://evil.test/callback", "http://127.0.0.1/api/models/oauth_callback", "http://127.0.0.1:bad/api/models/oauth_callback"]:
            with patch.dict(os.environ, {"FAFNIR_SKETCHFAB_REDIRECT_URI": redirect}), self.assertRaises(ModelError):
                self.oauth.start()
        for malformed in [{"access_token": None}, {"access_token": "token", "refresh_token": []}, {"access_token": "token", "expires_in": float("nan")}]:
            self.http.value = malformed
            with self.assertRaises(ModelError):
                self.oauth.finish("code", self.state())
            self.assertFalse(self.oauth.authenticated())
        self.oauth.access_token = "expired"
        self.oauth.expires_at = 0
        self.assertFalse(self.oauth.authenticated())
        with self.assertRaises(ModelError):
            self.oauth.token()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = GameStackApplication(self.root / "catalog.db")
        self.provider = Provider()
        self.catalog = ModelCatalog(self.root / "catalog.db", providers={"polyhaven": self.provider},
                                    evaluators={"fake": Evaluator()}, cache_root=self.root / "cache")
        self.app._model_catalog = self.catalog

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_cross_connection_download_claim_is_exclusive(self):
        plan = self.catalog.prepare_download({"model_id": MODEL["id"], "format": "glb"})
        second = ModelGraph(self.root / "catalog.db")
        try:
            with self.assertRaises(ModelError):
                second.claim_operation(plan["id"], "不正なnonce", "cc0")
            second.claim_operation(plan["id"], plan["approval_nonce"], "cc0")
            with self.assertRaisesRegex(ModelError, "already attempted"):
                self.catalog.download({"plan_id": plan["id"], "approval_nonce": plan["approval_nonce"], "accepted_license": "cc0"})
            self.assertEqual(self.catalog.graph.operation(plan["id"])["state"], "running")
            self.assertFalse(Path(plan["destination"]).exists())
        finally:
            second.close()

    def test_bad_evidence_and_uncalibrated_recommendation(self):
        self.catalog.detail(MODEL["id"])
        request = {"model_id": MODEL["id"], "project": "Test", "brief": "Iron pipe", "evaluator": "fake",
                   "observations": [{"kind": "visual", "result": "pass", "evidence": "Fixture review"}]}
        for change in [{"model_id": "license:cc0"}, {"evaluator": []}, {"observations": [{"kind": [], "result": "pass", "evidence": "bad"}]}]:
            with self.assertRaises(ModelError):
                self.catalog.evaluate({**request, **change})
        with patch.object(self.catalog.evaluators["fake"], "evaluate", side_effect=RuntimeError("private-backend-error")):
            with self.assertRaisesRegex(ModelError, "Decision provider failed") as error:
                self.catalog.evaluate(request)
            self.assertNotIn("private-backend-error", str(error.exception))
        recommendation = {**Evaluator().evaluate({}), "confidence_kind": "uncalibrated_probability"}
        with patch.object(self.catalog.evaluators["fake"], "evaluate", return_value=recommendation):
            self.assertEqual(self.catalog.evaluate(request)["proposed_decision"], "hold")
        for result in [None, [], {"probabilities": None}]:
            with self.assertRaises(ModelError):
                validate_result(result)
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-only"}), self.assertRaises(ModelError):
            JevEvaluator(FakeHTTP({"answers": None})).evaluate({})

    def test_http_origin_validation_and_saved_review_flow(self):
        server = create_server(self.app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        def post(action, payload, source=origin):
            headers = {"Content-Type": "application/json"}
            if source is not None:
                headers["Origin"] = source
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("POST", f"/api/models/{action}", json.dumps(payload), headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            for source in [None, "https://evil.test", "http://localhost:9"]:
                self.assertEqual(post("search", {"query": "pipe"}, source)[0], 403)
            self.assertEqual(post("search", {"query": "pipe"})[0], 200)
            self.assertEqual(post("evaluate", {"model_id": "license:cc0"})[0], 400)
            status, review = post("evaluate", {"model_id": MODEL["id"], "project": "QA", "brief": "Pipe"})
            self.assertEqual(status, 200)
            self.assertEqual(review["proposed_decision"], "hold")
            status, decision = post("decide", {"evaluation_id": review["id"], "decision": "hold", "reason": "Needs visual review"})
            self.assertEqual(status, 200)
            self.assertEqual(decision["project"], "QA")
            self.assertIn(decision["id"], {n["id"] for n in post("graph", {"model_id": MODEL["id"]})[1]["nodes"]})
            self.assertEqual(post("search", {"bad": True})[0], 400)
            from test_model_reference_auth import MemoryStore, CFG
            self.catalog.oauth = SketchfabOAuth(store=MemoryStore())
            setup = {**CFG, "redirect_uri": origin + "/api/models/oauth_callback", "remember": True}
            self.assertEqual(post("oauth_setup", setup, "https://evil.test")[0], 403)
            self.assertEqual(post("oauth_setup", {**setup,"redirect_uri":"http://127.0.0.1:1/api/models/oauth_callback"})[0], 400)
            self.assertEqual(post("oauth_setup", {**setup,"remember":"yes"})[0], 400)
            status, configured = post("oauth_setup", setup)
            self.assertEqual(status, 200)
            self.assertNotIn("fixture-secret", json.dumps(configured))
            self.assertTrue(post("status", {})[1]["sketchfab_oauth_configured"])
            self.assertEqual(post("oauth_disconnect", {})[0], 200)
            self.assertFalse(post("status", {})[1]["sketchfab_oauth_configured"])
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            with patch.object(self.catalog.oauth, "finish", return_value={"authenticated": True}) as finish:
                connection.request("GET", "/api/models/oauth_callback?code=fixture&state=fixture-state")
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertEqual(response.getheader("Location"), "/models.html")
                response.read()
                finish.assert_called_once_with("fixture", "fixture-state")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_cli_real_json_contract_without_network(self):
        request = self.root / "request.json"
        request.write_text(json.dumps({"query": "nothing", "source": "local"}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--db", str(self.root / "cli.db"), "--json", "models", "search", "--request", str(request)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["models"], [])


class ModelMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_discovery_and_model_workflow(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(GameStackApplication, "enable_automatic_maintenance"):
            server, app = build_mcp_server(str(Path(directory) / "catalog.db"))
            app._model_catalog = ModelCatalog(Path(directory) / "catalog.db", providers={"polyhaven": Provider()})
            try:
                async with Client(server) as client:
                    tools = {t.name: t for t in (await client.list_tools()).tools}
                    self.assertTrue(tools["get_3d_model_graph"].annotations.read_only_hint)
                    self.assertFalse(tools["download_3d_model"].annotations.destructive_hint)
                    found = await client.call_tool("search_3d_models", {"query": "pipe"})
                    self.assertFalse(found.is_error)
                    self.assertEqual(found.structured_content["models"][0]["id"], MODEL["id"])
                    evaluated = await client.call_tool("evaluate_3d_model", {"model_id": MODEL["id"], "project": "MCP QA", "brief": "Pipe"})
                    self.assertFalse(evaluated.is_error)
                    self.assertEqual(evaluated.structured_content["proposed_decision"], "hold")
                    saved = await client.call_tool("record_3d_model_decision", {"evaluation_id": evaluated.structured_content["id"], "decision": "hold", "reason": "Visual check pending"})
                    self.assertFalse(saved.is_error)
                    self.assertEqual(saved.structured_content["project"], "MCP QA")
            finally:
                app.close()
