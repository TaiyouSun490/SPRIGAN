import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from game_stack_planner.model_catalog import ModelCatalog
from game_stack_planner.model_sources import ModelError
from game_stack_planner.nvidia_simready import NvidiaSimReady

SHA = "a" * 40
ITEM = "food_produce_banana_j01"


class Response(io.BytesIO):
    def __init__(self, value, link=""):
        super().__init__(json.dumps(value).encode())
        self.headers = {"Link": link}


class FakeHTTP:
    def __init__(self):
        self.fail = False
        self.license = "CC-BY-4.0"
        self.preview = True

    def json(self, url, hosts):
        if self.fail:
            raise ModelError("offline")
        if "expand" in url:
            return {"sha": SHA}
        return {"license": self.license}

    def request(self, url, hosts):
        if self.fail:
            raise ModelError("offline")
        parsed = urlsplit(url)
        if ".thumbs" in parsed.path:
            folder = f"{ITEM}/simready_usd/.thumbs/256x256/"
            files = [{"type": "file", "path": folder + "../bad.usd.png"}]
            if self.preview:
                files.append({"type": "file", "path": folder + "sm_food_produce_banana_j01_01.usd.png"})
            return Response(files)
        cursor = parse_qs(parsed.query).get("cursor", [""])[0]
        if cursor:
            return Response([{"type": "directory", "path": "wood_crate_a01"}, {"type": "file", "path": "README.md"}])
        next_url = f"https://huggingface.co/api/datasets/nvidia/simready-assets/tree/{SHA}?cursor=next"
        return Response([{"type": "directory", "path": ITEM}, {"type": "directory", "path": "../unsafe"}], f'<{next_url}>; rel="next"')


class NvidiaIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = ModelCatalog(Path(self.tmp.name) / "models.db", providers={"stub": object()})
        self.http = FakeHTTP()
        self.provider = NvidiaSimReady(self.catalog.graph, self.http)
        self.catalog.providers["nvidia_simready"] = self.provider

    def tearDown(self):
        self.catalog.graph.close()
        self.tmp.cleanup()

    def test_index_pagination_search_and_offline_survival(self):
        self.assertEqual(self.provider.refresh()["count"], 2)
        rows, cursor = self.provider.search("", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(cursor, "1")
        self.assertEqual(self.provider.search("木", 2)[0][0]["external_id"], "wood_crate_a01")
        self.assertEqual(self.provider.search("banana", 2)[0][0]["license"], "unknown")
        self.http.fail = True
        with self.assertRaises(ModelError):
            self.provider.refresh()
        self.assertEqual(self.provider.state()["count"], 2)
        self.assertEqual(len(self.provider.search("banana", 2)[0]), 1)
        with self.assertRaises(ModelError):
            self.provider.search("", 1, "-1")

    def test_detail_requires_evidence_and_does_not_claim_cc0_or_download(self):
        self.provider.refresh()
        detail = self.catalog.detail("nvidia_simready:" + ITEM)
        self.assertEqual(detail["license"], "cc-by")
        self.assertEqual(detail["license_url"], "https://creativecommons.org/licenses/by/4.0/")
        self.assertIn("sm_food_produce_banana_j01_01.usd.png", detail["thumbnail"])
        self.assertFalse(detail["downloadable"])
        self.assertTrue(detail["external_download"])
        self.assertNotIn("..", detail["thumbnail"])
        with self.assertRaises(ModelError):
            self.catalog.prepare_download({"model_id": detail["id"]})
        self.http.license = "custom"
        self.http.preview = False
        changed = self.provider.detail(ITEM)
        self.assertEqual(changed["license"], "unknown")
        self.assertIsNone(changed["thumbnail"])
        with self.assertRaises(ModelError):
            self.provider.detail("not_indexed")

    def test_search_and_refresh_are_exposed_in_catalog(self):
        result = self.catalog.dispatch("refresh_nvidia_index", {})
        self.assertEqual(result["count"], 2)
        result = self.catalog.search({"query": "banana", "source": "nvidia_simready", "limit": 12})
        self.assertEqual(result["sources"]["nvidia_simready"]["status"], "ok")
        self.assertEqual(result["models"][0]["id"], "nvidia_simready:" + ITEM)
        with self.assertRaises(ModelError):
            self.catalog.dispatch("refresh_nvidia_index", {"url": "https://example.org"})

    def test_changed_dataset_revision_invalidates_old_detail(self):
        self.provider.refresh()
        model = self.catalog.detail("nvidia_simready:" + ITEM)
        self.assertTrue(model["detail_fetched"])
        revised = self.provider.normalize(ITEM, "b" * 40)
        saved = self.catalog.graph.save_model(revised)
        self.assertFalse(saved["detail_fetched"])
        self.assertEqual(saved["license"], "unknown")
        self.assertEqual(saved["dataset_revision"], "b" * 40)


if __name__ == "__main__":
    unittest.main()
