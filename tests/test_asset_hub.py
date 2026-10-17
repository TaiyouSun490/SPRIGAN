"""Owned/CC0 boundaries, acquisition, billing, authentication and public API contracts."""
from __future__ import annotations
import copy
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from game_stack_planner.api import GameStackApplication
from game_stack_planner.asset_hub import AssetHub, inspect_archive
from game_stack_planner.asset_hub_generators import GenerationCoordinator, request_spec
from game_stack_planner.asset_hub_sources import GENERATORS, STORES, cc0, public_url, store_url
from game_stack_planner.model_sources import ModelError
from game_stack_planner.native_messaging import handle_message
from game_stack_planner.server import create_server


PUBLIC = {"id":"polyhaven:forest", "title":"Forest", "type":"hdri", "url":"https://polyhaven.com/a/forest", "license":{"name":"CC0","attributionRequired":False},"formats":["hdr"],"downloadable":True}


class FakeHTTP:
    def __init__(self):
        self.calls=[]
        self.value={"code":0,"data":{"task_id":"task123"}}
    def json(self,url,hosts,**kwargs):
        self.calls.append((url,kwargs))
        return copy.deepcopy(self.value)


class Upstream:
    base="https://3d.shep.bot"
    def __init__(self):
        self.http=FakeHTTP()
        self.rows=[copy.deepcopy(PUBLIC)]
        self.fail=False
    def providers(self):
        return [{"id":"polyhaven","name":"Poly Haven","supportsDownload":True}]
    def search(self,*args):
        if self.fail:raise ModelError("offline")
        return {"results":copy.deepcopy(self.rows),"providers":[{"provider":"polyhaven","status":"ok"}]}
    def detail(self,*args):
        return copy.deepcopy(PUBLIC),{"files":[{"filename":"forest.hdr","url":"https://cdn.example/file?secret=private"}]}
    def bundle(self,*args):
        return "https://cdn.example/forest.hdr"


class Response(io.BytesIO):
    def __init__(self,data,expected=None):
        super().__init__(data)
        self.headers={"Content-Length":str(len(data) if expected is None else expected)}


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.up=Upstream()
        self.hub=AssetHub(self.root/"catalog.db",upstream=self.up,cache_root=self.root/"cache")
        self.env=patch.dict(os.environ,{"USERPROFILE":str(self.root),"HOME":str(self.root),"LOCALAPPDATA":str(self.root/"appdata")},clear=True);self.env.start()
    def tearDown(self):
        self.env.stop();self.hub.close();self.temp.cleanup()
    def own(self,source="booth",**row):
        return self.hub.import_owned({"source":source,"owned_confirmation":True,"items":[{"title":"Forest audio","url":"https://shop.booth.pm/items/123","type":"sfx",**row}]})["assets"][0]
    def prepare(self):
        self.hub.search({"query":"forest"})
        return self.hub.prepare_download({"asset_id":PUBLIC["id"],"format":"hdr","max_bytes":1024})

    def test_public_only_cc0_and_owned_first_when_source_is_offline(self):
        self.up.rows.extend([{**PUBLIC,"id":"polyhaven:not-owned","license":{"name":"CC BY"}}, {**PUBLIC,"id":"polyhaven:custom","license":{"name":"CC0 custom restrictions"}}])
        owned=self.own()
        found=self.hub.search({"query":"forest"})["results"]
        self.assertEqual([v["rights"]["basis"] for v in found],["owned","cc0"])
        self.assertFalse(found[0]["rights"]["purchase_verified"])
        self.up.fail=True
        result=self.hub.search({"query":"forest"})
        self.assertIn(owned["id"],[v["id"] for v in result["results"]])
        self.assertEqual(result["providers"][0]["status"],"error")
        self.assertEqual(len(self.hub.search({"query":"forest","rights":"owned","online":False})["results"]),1)

    def test_import_is_atomic_idempotent_and_not_purchase_verification(self):
        first=self.own();second=self.own()
        self.assertEqual(first["id"],second["id"])
        self.assertEqual(len(self.hub.search({"online":False})["results"]),1)
        with self.assertRaises(ModelError):self.hub.import_owned({"source":"booth","owned_confirmation":False,"items":[]})
        with self.assertRaises(ModelError):self.hub.import_owned({"source":"booth","owned_confirmation":True,"items":[{"title":"New","url":"https://booth.pm/items/456"},{"title":"Bad","url":"https://booth.pm/items/789","cookie":"secret"}]})
        self.assertEqual(len(self.hub.search({"online":False})["results"]),1)
        for source,config in STORES.items():
            with self.subTest(source=source):
                result=self.hub.import_owned({"source":source,"owned_confirmation":True,"items":[{"title":"Own SFX","url":config[1].rstrip("/")+"/product/example","type":"sfx"}]})
                self.assertFalse(result["purchase_verified"])

    def test_private_order_urls_and_wrong_domains_are_rejected(self):
        for source,url in [("booth","https://booth.pm/orders/secret"),("humble","https://humblebundle.com/downloads?key=secret"),("booth","https://evil.example/items/1"),("fab","https://www.fab.com/library")]:
            with self.subTest(url=url),self.assertRaises(ModelError):store_url(source,url)
        self.assertEqual(store_url("booth","https://booth.pm/items/1?tracking=123"),"https://booth.pm/items/1")
        for url in ["http://example.com/x","https://127.0.0.1/x","https://[::1]/x","https://user:secret@example.com/x","https://192.168.0.1/x","https://localhost/x"]:
            with self.assertRaises(ModelError):public_url(url)

    def test_owned_product_identity_is_stable_across_booth_shop_and_locale_urls(self):
        one=self.own()
        two=self.own(url="https://booth.pm/ja/items/123?tracking=link")
        self.assertEqual(one["id"],two["id"])
        self.assertEqual(len(self.hub.search({"rights":"owned","online":False})["results"]),1)

    def test_local_root_scope_engine_restriction_and_audio_manifest(self):
        folder=self.root/"audio";folder.mkdir();(folder/"footsteps.wav").write_bytes(b"RIFF-audio")
        registered=self.hub.register_root({"source":"booth","path":str(folder)})
        own=self.own(root_id=registered["id"],file="footsteps.wav",license="creator terms",license_reviewed=True,engine_restriction="unreal")
        self.assertFalse(self.hub.export({"asset_ids":[own["id"]],"target":"unity"})["ready"])
        self.assertTrue(self.hub.export({"asset_ids":[own["id"]],"target":"unreal"})["ready"])
        with self.assertRaises(ModelError):self.hub.local_file(registered["id"],"../secret.wav")
        with self.assertRaises(ModelError):self.hub.register_root({"source":"booth","path":str(Path.home())})

    def test_download_cap_checksum_idempotence_and_no_signed_url_persistence(self):
        p=self.prepare()
        self.assertNotIn("secret=private",json.dumps(p))
        self.hub.http_open=lambda *a,**k:Response(b"HDRDATA")
        request={"operation_id":p["id"],"approval_nonce":p["approval_nonce"],"accepted_license":"CC0"}
        with patch("game_stack_planner.asset_hub.public_url",side_effect=lambda url,**kwargs:url):
            result=self.hub.download(request)
            self.assertEqual(self.hub.download(request)["state"],"complete")
        record=result["receipt"]["files"][0]
        self.assertEqual(len(record["sha256"]),64)
        self.assertEqual(self.hub.resolve_file(record).read_bytes(),b"HDRDATA")
        self.assertNotIn("nonce",self.hub.operation({"operation_id":p["id"]}))
        self.hub.search({"query":"forest"})
        self.assertTrue(self.hub.reusable_files(PUBLIC["id"],"hdr","2k")["reused"])
        self.assertIsNone(self.hub.reusable_files(PUBLIC["id"],"hdr","4k"))

    def test_truncated_download_fails_and_archive_traversal_is_rejected(self):
        p=self.prepare();self.hub.http_open=lambda *a,**k:Response(b"short",expected=100)
        with patch("game_stack_planner.asset_hub.public_url",side_effect=lambda url,**kwargs:url),self.assertRaisesRegex(ModelError,"incomplete"):
            self.hub.download({"operation_id":p["id"],"approval_nonce":p["approval_nonce"],"accepted_license":"CC0"})
        self.assertEqual(self.hub.operation({"operation_id":p["id"]})["state"],"failed")
        self.assertFalse(Path(p["destination"]).exists())
        path=self.root/"bad.zip"
        with zipfile.ZipFile(path,"w") as z:z.writestr("../escape.glb",b"x")
        with self.assertRaises(ModelError):inspect_archive(path,10000)

    def test_data_only_archive_extraction_preserves_gltf_companions(self):
        blob=io.BytesIO()
        with zipfile.ZipFile(blob,"w") as z:
            z.writestr("mesh/model.gltf","{}")
            z.writestr("mesh/textures/color.png",b"PNG")
            z.writestr("mesh/mesh.bin",b"mesh")
            z.writestr("execute.py","raise Exception()")
        p=self.prepare();self.hub.http_open=lambda *a,**k:Response(blob.getvalue())
        with patch("game_stack_planner.asset_hub.public_url",side_effect=lambda url,**kwargs:url):
            result=self.hub.download({"operation_id":p["id"],"approval_nonce":p["approval_nonce"],"accepted_license":"CC0"})
        paths=[v["relative"] for v in result["receipt"]["files"]]
        self.assertTrue(any(v.endswith("contents/mesh/model.gltf") for v in paths))
        self.assertTrue(any(v.endswith("contents/mesh/textures/color.png") for v in paths))
        self.assertFalse(any(v.endswith("execute.py") for v in paths))

    def test_generation_contracts_paid_consent_single_submission_and_no_key_leak(self):
        self.hub.credentials.setup("tripo","test-only-secret",False)
        coordinator=GenerationCoordinator(self.hub)
        p=coordinator.prepare_generation({"provider":"tripo","type":"model","prompt":"Mossy rock","entitlement_note":"Test fixture plan"})
        req={"operation_id":p["id"],"approval_nonce":p["approval_nonce"]}
        with self.assertRaises(ModelError):coordinator.generate(req)
        result=coordinator.generate({**req,"accept_provider_billing":True})
        self.assertEqual(result["state"],"running")
        self.assertEqual(self.up.http.calls[0][1]["headers"]["Authorization"],"Bearer test-only-secret")
        with self.assertRaises(ModelError):coordinator.generate({**req,"accept_provider_billing":True})
        self.assertEqual(len(self.up.http.calls),1)
        self.assertNotIn("test-only-secret",json.dumps(self.hub.status()))
        self.up.http.value={"code":0,"data":{"status":"success","progress":100}}
        self.assertEqual(coordinator.poll_generation({"operation_id":p["id"]})["state"],"output_ready")

    def test_paid_timeout_remains_unknown_and_suno_has_no_invented_api(self):
        self.hub.credentials.setup("tripo","fixture",False)
        c=GenerationCoordinator(self.hub)
        p=c.prepare_generation({"provider":"tripo","type":"model","prompt":"Rock","entitlement_note":"Fixture"})
        req={"operation_id":p["id"],"approval_nonce":p["approval_nonce"],"accept_provider_billing":True}
        with patch.object(self.up.http,"json",side_effect=TimeoutError()),self.assertRaises(ModelError):c.generate(req)
        self.assertEqual(self.hub.operation({"operation_id":p["id"]})["state"],"submission_unknown")
        with self.assertRaises(ModelError):c.generate(req)
        with self.assertRaises(ModelError):c.prepare_generation({"provider":"suno","type":"music","prompt":"Music","entitlement_note":"Fixture"})

    def test_api_body_contracts_and_invalid_parameters(self):
        self.assertEqual(request_spec("marble","world","Forest",{})["body"]["world_prompt"],{"type":"text","text_prompt":"Forest"})
        for kind,options,path in [("sfx",{"duration_seconds":3},"sound-generation"),("music",{"music_length_ms":10000},"music"),("voice",{"voice_id":"abc123"},"text-to-speech/abc123")]:
            self.assertTrue(request_spec("elevenlabs",kind,"Test",options)["url"].endswith(path))
        for options in [{"duration_seconds":0},{"duration_seconds":31},{"duration_seconds":float("nan")},{"token":"secret"}]:
            with self.assertRaises(ModelError):request_spec("elevenlabs","sfx","Test",options)
        self.assertFalse(cc0({"license":{"name":"CC0 custom terms"}}))

    def test_explicit_local_asset_server_download_does_not_allow_other_local_ports(self):
        self.up.base="http://127.0.0.1:8787";self.up.local=True
        self.hub.http_open=lambda *a,**k:Response(b"fixture")
        path=self.root/"asset.bin"
        self.assertEqual(self.hub.fetch(self.up.base+"/v1/assets/test:one/download",path,100),7)
        with self.assertRaises(ModelError):self.hub.fetch("http://127.0.0.1:9999/private",self.root/"bad.bin",100)

    def test_rights_review_does_not_create_cc0_or_cross_engine_ownership(self):
        asset=self.own()
        with self.assertRaises(ModelError):self.hub.review_rights({"asset_id":asset["id"],"license":"free","engine_restriction":"none","entitlement_note":"Test","review_confirmation":True})
        reviewed=self.hub.review_rights({"asset_id":asset["id"],"license":"Creator licence","engine_restriction":"unreal","entitlement_note":"User checked the purchased licence","review_confirmation":True})
        self.assertEqual(reviewed["rights"]["basis"],"owned")
        self.assertFalse(reviewed["rights"]["purchase_verified"])
        self.assertFalse(self.hub.export({"asset_ids":[asset["id"]],"target":"unity"})["ready"])

    @unittest.skipUnless(os.name=="nt","Windows DPAPI persistence")
    def test_sketchfab_saved_connection_reloads_across_fafnir_processes(self):
        from game_stack_planner.model_credentials import ProtectedOAuthStore
        from game_stack_planner.model_oauth import SketchfabOAuth
        store=ProtectedOAuthStore(self.root/"fixture.dpapi")
        http=FakeHTTP();http.value={"access_token":"only-test-access","refresh_token":"only-test-refresh","expires_in":3600}
        one=SketchfabOAuth(http,store)
        one.setup({"client_id":"only-test-client","client_secret":"only-test-secret","redirect_uri":"http://127.0.0.1:8770/api/models/oauth_callback"},True)
        two=SketchfabOAuth(http,store)
        self.assertFalse(two.authenticated())
        one._exchange({"grant_type":"test-only"})
        self.assertEqual(two.token(),"only-test-access")
        one.disconnect()
        self.assertEqual(two.token(),"")
        with self.assertRaises(ModelError):two.start("https://evil.example")


class HubHttpTests(unittest.TestCase):
    def test_http_origin_secret_entry_preview_and_browser_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            app=GameStackApplication(Path(temp)/"db.sqlite")
            app._asset_hub=AssetHub(app.repository.path,upstream=Upstream(),cache_root=Path(temp)/"cache")
            server=create_server(app,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin=f"http://127.0.0.1:{server.server_port}"
            def post(action,payload,source=origin):
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
                headers={"Content-Type":"application/json"}
                if source:headers["Origin"]=source
                connection.request("POST","/api/hub/"+action,json.dumps(payload),headers)
                response=connection.getresponse();value=response.read();connection.close()
                return response.status,json.loads(value)
            try:
                self.assertEqual(post("status",{},None)[0],403)
                self.assertEqual(post("status",{},"https://evil.example")[0],403)
                code,value=post("connection_setup",{"provider":"tripo","key":"only-test-key","remember":False})
                self.assertEqual(code,200);self.assertNotIn("only-test-key",json.dumps(value))
                self.assertEqual(post("connection_setup",{"provider":"suno","key":"bad","remember":False})[0],400)
                context=handle_message({"action":"get_asset_hub_capture_context","schema_version":1,"url":"https://shop.booth.pm/items/123?tracking=test","title":"Forest SFX"},app)
                self.assertEqual(context["product"]["source"],"booth")
                self.assertNotIn("tracking",context["product"]["url"])
                synced=handle_message({"action":"sync_asset_hub_owned","schema_version":1,"source":"booth","items":[{"title":"Forest SFX","url":"https://shop.booth.pm/items/123","type":"sfx"}],"owned_confirmation":True},app)
                self.assertEqual(synced["count"],1)
                self.assertEqual(post("search",{"online":False})[1]["results"][0]["title"],"Forest SFX")
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
                connection.request("GET","/v1/search?q=forest&online=false&rights=owned")
                response=connection.getresponse();self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())["results"][0]["source"],"booth")
                connection.close()
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5);app.close()


class HubMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_unified_tools_are_callable_over_real_mcp_transport(self):
        from mcp.client import Client
        from game_stack_planner.mcp_server import build_mcp_server
        with tempfile.TemporaryDirectory() as temp:
            server,app=build_mcp_server(str(Path(temp)/"catalog.db"))
            app._asset_hub=AssetHub(app.repository.path,upstream=Upstream(),cache_root=Path(temp)/"cache")
            try:
                async with Client(server) as client:
                    names={v.name for v in (await client.list_tools()).tools}
                    self.assertTrue({"search_assets","get_asset","download_asset","list_providers","generate_asset","sync_owned_asset_manifest","record_asset_usage_rights"}.issubset(names))
                    result=await client.call_tool("search_assets",{"query":"forest","online":True,"rights":"cc0"})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["results"][0]["license"]["name"],"CC0")
                    invalid=await client.call_tool("get_asset_hub_status",{"key":"not-an-accepted-field"})
                    # The SDK ignores surplus arguments; the tool neither stores nor echoes them.
                    self.assertNotIn("not-an-accepted-field",json.dumps(invalid.structured_content))
            finally:app.close()


if __name__=="__main__":unittest.main()
