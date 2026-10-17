"""Owned-library pagination receipts, crash recovery and curation invariants."""
import json
from pathlib import Path
import tempfile
import unittest

from game_stack_planner.api import GameStackApplication
from game_stack_planner.asset_hub import AssetHub
from game_stack_planner.model_sources import ModelError
from game_stack_planner.native_messaging import handle_message


class LibrarySyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "catalog.db"
        self.hub = AssetHub(self.database)
        self.sync = self.hub.library_sync

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def begin(self, source="booth", id="a" * 32):
        return self.sync.begin_library_sync({"run_id": id, "source": source, "owned_confirmation": True})

    def page(self, rows=None, **kw):
        return self.sync.apply_library_sync_page({"run_id": "a" * 32, "page_index": 0,
            "items": rows if rows is not None else [{"source_product_id": "123", "title": "Forest pack"}], "terminal": False, **kw})

    def test_deduplicates_across_pages_and_replay_after_restart(self):
        self.begin()
        self.assertEqual(self.page()["count"], 1)
        self.hub.close()
        self.hub = AssetHub(self.database)
        self.sync = self.hub.library_sync
        self.assertTrue(self.page()["replayed"])
        result = self.page(page_index=1, terminal=True, coverage="booth_library_and_gifts_end")
        self.assertEqual((result["pages"], result["count"], result["complete"]), (2, 1, True))
        self.assertEqual(len(self.hub.status()["syncs"]), 1)
        self.assertTrue(self.page(page_index=1, terminal=True, coverage="booth_library_and_gifts_end")["replayed"])
        self.assertEqual(len(self.hub.status()["syncs"]), 1)

    def test_committed_page_must_replay_exactly_and_sequence_cannot_skip(self):
        self.begin()
        with self.assertRaises(ModelError): self.page(page_index=1)
        self.page()
        with self.assertRaises(ModelError): self.page([{ "source_product_id": "456", "title": "Changed page"}])
        self.assertEqual(self.sync.library_sync_status({"run_id": "a"*32})["pages"], 1)

    def test_malformed_batch_is_atomic_and_does_not_advance(self):
        self.begin()
        with self.assertRaises(ModelError):
            self.page([{"source_product_id": "123", "title": "Valid"}, {"source_product_id": "456", "title": "Bad", "gamekey": "private"}])
        self.assertEqual(self.hub.search({"online": False})["results"], [])
        self.assertEqual(self.sync.library_sync_status({"run_id": "a"*32})["pages"], 0)
        self.assertEqual(self.page()["pages"], 1)

    def test_resume_requires_running_and_finished_run_cannot_resume(self):
        self.begin()
        self.sync.set_library_sync_state({"run_id": "a"*32, "state": "paused"})
        with self.assertRaises(ModelError): self.page()
        self.sync.set_library_sync_state({"run_id": "a"*32, "state": "running"})
        self.page(terminal=True, coverage="booth_library_and_gifts_end")
        with self.assertRaises(ModelError): self.sync.set_library_sync_state({"run_id": "a"*32, "state": "running"})

    def test_completion_requires_correct_source_terminal_and_empty_is_valid(self):
        self.begin()
        for coverage in ("", "fab_acquired_cursor_end"):
            with self.assertRaises(ModelError): self.page([], terminal=True, coverage=coverage)
        self.assertTrue(self.page([], terminal=True, coverage="booth_library_and_gifts_end")["complete"])

    def test_preserves_user_rights_files_tags_and_does_not_delete_unseen(self):
        folder = Path(self.temp.name) / "owned"
        folder.mkdir()
        (folder / "birds.wav").write_bytes(b"RIFF")
        root = self.hub.register_root({"source": "booth", "path": str(folder)})
        self.hub.import_owned({"source": "booth", "owned_confirmation": True, "items": [
            {"title": "Edited title", "url": "https://shop.booth.pm/items/123", "type": "sfx", "tags": ["my-tag"], "description": "my notes", "file": "birds.wav", "root_id": root["id"]},
            {"title": "Not in current page", "url": "https://booth.pm/items/999"}]})
        self.hub.review_rights({"asset_id": "booth:123", "license": "creator terms", "license_url": "", "engine_restriction": "none", "entitlement_note": "Checked licence", "review_confirmation": True})
        self.begin()
        self.page(terminal=True, coverage="booth_library_and_gifts_end")
        item = self.hub.asset("booth:123")
        self.assertEqual(item["rights"]["entitlement_note"], "Checked licence")
        self.assertTrue(item["rights"]["license_reviewed"])
        self.assertEqual(item["license"]["name"], "creator terms")
        self.assertEqual((item["type"], item["tags"], item["description"]), ("sfx", ["my-tag"], "my notes"))
        self.assertEqual(item["local_files"][0]["relative"], "birds.wav")
        self.assertTrue(item["downloadable"])
        self.assertEqual(self.hub.asset("booth:999")["title"], "Not in current page")

    def test_removed_fab_and_humble_bundle_items_need_no_guessed_public_link(self):
        uid = "11111111-2222-3333-4444-555555555555"
        for source, product, coverage, run_id in [
            ("fab", uid, "fab_acquired_cursor_end", "a" * 32),
            ("humble", "forest_sound_effects", "humble_all_orders_end", "b" * 32)]:
            with self.subTest(source=source):
                self.begin(source, run_id)
                self.page([{"source_product_id": product, "title": "Owned delisted item"}], run_id=run_id, terminal=True, coverage=coverage)
                item = self.hub.asset(source + ":" + product)
                self.assertEqual(item["url"], "")
                self.assertFalse(item["rights"]["purchase_verified"])

    def test_humble_machine_id_stable_when_public_link_appears_later(self):
        self.begin("humble")
        self.page([{"source_product_id":"forest_pack","title":"Pack"}])
        self.page([{"source_product_id":"forest_pack","title":"Pack", "url":"https://www.humblebundle.com/store/forest"}], page_index=1, terminal=True, coverage="humble_all_orders_end")
        self.assertEqual(self.hub.status()["library_syncs"][0]["count"], 1)

    def test_private_urls_source_mismatch_and_excess_rows_fail(self):
        self.begin()
        for row in [
            {"source_product_id": "123", "title": "x", "url": "https://accounts.booth.pm/library"},
            {"source_product_id": "123", "title": "x", "url": "https://booth.pm/items/456"},
            {"source_product_id": "123", "title": "x", "url": "https://evil.example/items/123"},
            {"source_product_id": "123", "title": "x", "cookie": "private"}]:
            with self.subTest(row=row), self.assertRaises(ModelError): self.page([row])
        with self.assertRaises(ModelError): self.page([{"source_product_id":"123","title":"x"}] * 201)

    def test_new_source_licence_fills_unknown_but_does_not_reset_a_review(self):
        uid = "11111111-2222-3333-4444-555555555555"
        self.begin("fab")
        self.page([{"source_product_id": uid, "title": "Pack"}])
        self.page([{"source_product_id": uid, "title": "Pack", "license": "Fab Standard"}], page_index=1)
        self.assertEqual(self.hub.asset("fab:"+uid)["license"]["name"], "Fab Standard")
        self.assertFalse(self.hub.asset("fab:"+uid)["rights"]["license_reviewed"])

    def test_second_native_connection_can_retry_same_committed_page(self):
        self.begin()
        self.page()
        other = AssetHub(self.database)
        try:
            result = other.library_sync.apply_library_sync_page({"run_id":"a"*32,"page_index":0,"items":[{"source_product_id":"123","title":"Forest pack"}],"terminal":False})
            self.assertTrue(result["replayed"])
            self.assertEqual(result["count"], 1)
        finally: other.close()

    def test_run_source_is_immutable_and_consent_required(self):
        self.begin()
        with self.assertRaises(ModelError): self.begin("fab")
        with self.assertRaises(ModelError): self.sync.begin_library_sync({"run_id":"b"*32,"source":"humble","owned_confirmation":False})
        self.assertEqual(self.begin()["id"], "a"*32)

    def test_native_boundary_rejects_credential_and_private_cursor_fields(self):
        app = GameStackApplication(Path(self.temp.name) / "native.db")
        try:
            begin = {"action":"hub_library_begin","schema_version":1,"run_id":"a"*32,"source":"humble","owned_confirmation":True}
            self.assertTrue(handle_message(begin, app)["ok"])
            with self.assertRaises(Exception): handle_message({**begin,"cookies":"private"}, app)
            page = {"action":"hub_library_page","schema_version":1,"run_id":"a"*32,"page_index":0,"items":[{"source_product_id":"sound_pack","title":"SFX"}],"terminal":True,"coverage":"humble_all_orders_end"}
            self.assertTrue(handle_message(page, app)["result"]["complete"])
            with self.assertRaises(Exception): handle_message({**page, "cursor": "private-order-key"}, app)
            self.assertNotIn("private", json.dumps(handle_message({"action":"hub_library_status","schema_version":1}, app)))
        finally: app.close()


if __name__ == "__main__": unittest.main()
