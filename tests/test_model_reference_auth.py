"""Reference evidence and local OAuth setup regression tests. No real credentials/network."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from game_stack_planner.model_credentials import ProtectedOAuthStore
from game_stack_planner.model_oauth import SketchfabOAuth
from game_stack_planner.model_reference import normalize_profile, checks
from game_stack_planner.model_decisions import JevEvaluator
from game_stack_planner.model_catalog import ModelCatalog
from game_stack_planner.model_sources import ModelError
from test_model_discovery import MODEL, Provider, FakeHTTP, Evaluator

PROFILE={"sources":[{"id":"frame", "source":"reference.png", "note":"Observed cylindrical metal body; exact length unknown."}],
         "criteria":[{"axis":"material", "expected":"Metal housing with wear", "observed":"Metal housing with paint loss", "basis":"observed", "evidence_ids":["frame"], "result":"unknown"}]}
VOTE={"choice":"pass", "confidence":.95, "probabilities":{"pass":.95,"fail":.01,"unknown":.04}}
CFG={"client_id":"fixture-client", "client_secret":"fixture-secret", "redirect_uri":"http://127.0.0.1:8770/api/models/oauth_callback"}

class MemoryStore:
    supported=True
    def __init__(self):self.value=None
    def load(self):return copy.deepcopy(self.value)
    def save(self,value):self.value=copy.deepcopy(value)
    def clear(self):self.value=None

class ReferenceTests(unittest.TestCase):
    def test_grounding_and_explicit_failure_cannot_be_overridden(self):
        self.assertEqual(checks(PROFILE,{"material":VOTE},.8,True)[0]["result"],"pass")
        for changes in [{"basis":"inferred"},{"observed":""},{"evidence_ids":[]},{"basis":"unknown"}]:
            p=copy.deepcopy(PROFILE);p["criteria"][0].update(changes)
            self.assertEqual(checks(p,{"material":VOTE},.8,True)[0]["result"],"unknown")
        p=copy.deepcopy(PROFILE);p["criteria"][0]["result"]="fail"
        self.assertEqual(checks(p,{"material":VOTE},.8,True)[0]["result"],"fail")
        self.assertEqual(checks(PROFILE,{"material":VOTE},.99,True)[0]["result"],"unknown")
        self.assertEqual(checks(PROFILE,{"material":VOTE},.8,False)[0]["result"],"unknown")

    def test_profile_validation_and_no_implicit_source_fetch(self):
        self.assertEqual(normalize_profile(PROFILE),PROFILE)
        for alteration in [{"axis":"bogus"},{"evidence_ids":["invented"]},{"basis":[]},{"observed":"x"*3001}]:
            p=copy.deepcopy(PROFILE);p["criteria"][0].update(alteration)
            with self.assertRaises(ModelError):normalize_profile(p)
        for p in [[],{}, {"sources":[],"criteria":[]}, {"sources":PROFILE["sources"]*2,"criteria":PROFILE["criteria"]}]:
            with self.assertRaises(ModelError):normalize_profile(p)

    def test_jev_questions_and_missing_answer_fail_closed(self):
        adoption=Evaluator().evaluate({})
        data={"answers":{"adoption":{"type":"choice",**adoption},"fit_material":{"type":"choice",**VOTE}}}
        http=FakeHTTP(data)
        with patch.dict(os.environ,{"TYPESAFE_API_KEY":"fixture-only"},clear=True):
            evaluated=JevEvaluator(http).evaluate({"reference_profile":PROFILE})
            self.assertEqual(evaluated["reference_judgments"]["material"],VOTE)
            self.assertEqual(set(http.calls[-1][2]["payload"]["questions"]),{"adoption","fit_material"})
            del http.value["answers"]["fit_material"]
            with self.assertRaisesRegex(ModelError,"omitted"):JevEvaluator(http).evaluate({"reference_profile":PROFILE})

    def test_reference_changes_fingerprint_and_saved_graph_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            evaluator=Evaluator();catalog=ModelCatalog(Path(folder)/"test.db",providers={"polyhaven":Provider()},evaluators={"fake":evaluator})
            try:
                catalog.detail(MODEL["id"])
                request={"model_id":MODEL["id"],"project":"Cell","brief":"Working metal torch","evaluator":"fake","observations":[{"kind":"visual","result":"pass","evidence":"Observed model"}],"reference_profile":PROFILE}
                r=catalog.evaluate(request);self.assertEqual(r["proposed_decision"],"hold")
                response={**evaluator.evaluate({}),"reference_judgments":{"material":VOTE}}
                with patch.object(evaluator,"evaluate",return_value=response):
                    r=catalog.evaluate(request);self.assertEqual(r["proposed_decision"],"adopt")
                    changed=copy.deepcopy(PROFILE);changed["criteria"][0]["expected"]="Chrome housing"
                    other=catalog.evaluate({**request,"reference_profile":changed})
                    self.assertNotEqual(r["input_hash"],other["input_hash"])
                    failed=copy.deepcopy(PROFILE);failed["criteria"][0]["result"]="fail"
                    self.assertEqual(catalog.evaluate({**request,"reference_profile":failed})["proposed_decision"],"reject")
                self.assertEqual(catalog.graph.get(r["id"])["reference_profile"],PROFILE)
            finally:catalog.graph.close()

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)

    def test_config_connect_restart_refresh_forget(self):
        store=MemoryStore();http=FakeHTTP({"access_token":"fixture-access","refresh_token":"fixture-refresh","expires_in":60})
        first=SketchfabOAuth(http,store);self.assertEqual(first.setup(CFG,True),{"configured":True,"remembered":True})
        state=parse_qs(urlsplit(first.start()["authorize_url"]).query)["state"][0];first.finish("code",state)
        restored=SketchfabOAuth(http,store);self.assertTrue(restored.authenticated())
        self.assertEqual(restored.token(),"fixture-access");restored.expires_at=0
        http.value["access_token"]="fixture-renewed";restored.token();self.assertEqual(store.value["access_token"],"fixture-renewed")
        status=json.dumps(restored.storage_status());self.assertNotIn("fixture",status)
        restored.disconnect();self.assertIsNone(store.value);self.assertFalse(restored.configured());self.assertFalse(restored.authenticated())

    def test_session_only_and_bad_saved_state_recovery(self):
        store=MemoryStore();oauth=SketchfabOAuth(store=store);oauth.setup(CFG,False);self.assertIsNone(store.value)
        self.assertFalse(SketchfabOAuth(store=store).configured())
        store.value={"wrong":"shape"};oauth=SketchfabOAuth(store=store);self.assertTrue(oauth.storage_error)
        oauth.setup(CFG,True);self.assertFalse(oauth.storage_error)
        state=parse_qs(urlsplit(oauth.start()["authorize_url"]).query)["state"][0]
        oauth.setup({**CFG,"client_id":"changed"},False)
        with self.assertRaises(ModelError):oauth.finish("code",state)
        with self.assertRaises(ModelError):oauth.setup(CFG,"yes")

    @unittest.skipUnless(os.name=="nt","Windows user-scoped encryption")
    def test_actual_dpapi_roundtrip_without_plaintext_on_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProtectedOAuthStore(Path(folder)/"test.dpapi")
            value={"token":"FAFNIR-test-not-a-real-credential"};store.save(value)
            self.assertNotIn(value["token"].encode(),store.path.read_bytes())
            self.assertEqual(store.load(),value);store.clear();self.assertIsNone(store.load())

if __name__=="__main__":unittest.main()
