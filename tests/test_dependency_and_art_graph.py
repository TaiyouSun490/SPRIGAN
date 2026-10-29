from __future__ import annotations

import tempfile
import unittest
import json
from hashlib import sha256
from pathlib import Path

from game_stack_planner.api import ApiError, GameStackApplication
from game_stack_planner.art_styles import candidate_styles
from game_stack_planner.dependency_graph import plan_dependency_review, version_status
from game_stack_planner.compatibility import assess_candidate_compatibility, project_state_fingerprint
from game_stack_planner.models import Candidate, GameRequirement, ProjectSnapshot
from game_stack_planner.planning_graph import build_graph
from game_stack_planner.repository import StackRepository
from game_stack_planner.service import GameStackPlanner, _candidate_score
from game_stack_planner.unity_project import scan_unity_project


def asset(number, minimum):
    return Candidate(id=f"asset_store:{number}", source="asset_store", external_id=str(number), title=f"Inventory asset {number}", url="", categories=("inventory",), ownership="owned", metadata={"local_validation": {"package_inspection": {"package_manifests": [{"dependencies": {"com.example.shared": minimum}}]}}})


class DependencyAndArtTests(unittest.TestCase):
    def test_old_project_validation_is_not_applied_to_changed_state_or_platform(self):
        project = ProjectSnapshot(path="test", name="test", unity_version="6000.0.1", render_pipeline="urp", input_backend="both", packages=())
        fingerprint = sha256(str(Path(project.path).resolve()).encode("utf-8")).hexdigest()[:24]
        validation = {"project": {"fingerprint": fingerprint, "state_fingerprint": project_state_fingerprint(project), "platform": "pc"}, "overall_status": "incompatible", "static_checks": {"failures": ["failure"]}}
        candidate = Candidate(id="asset_store:11", source="asset_store", external_id="11", title="Example", url="", metadata={"local_validation": validation})
        self.assertEqual(assess_candidate_compatibility(candidate, project=project, platform="pc")["status"], "incompatible")
        other = assess_candidate_compatibility(candidate, project=project, platform="quest")
        self.assertEqual(other["status"], "unknown")
        self.assertTrue(any("再検査" in warning for warning in other["local_warnings"]))

    def test_scan_reads_transitive_lock_without_exposing_raw_git_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Packages").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "Packages" / "manifest.json").write_text(json.dumps({"dependencies": {"com.example.main": "1.0.0"}}))
            (root / "Packages" / "packages-lock.json").write_text(json.dumps({"dependencies": {"com.example.main": {"version": "1.0.0", "depth": 0, "dependencies": {"com.example.child": "1.0.0"}}, "com.example.child": {"version": "1.2.0", "depth": 1, "dependencies": {}}}}))
            (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
            project = scan_unity_project(root)
            self.assertIn("com.example.child", project.package_lock)
            self.assertEqual(project.render_pipeline_evidence, "package_presence_only")
            self.assertNotIn("package_lock", project.to_dict())
            req = GameRequirement("inventory", "inventory", "high", "main", "")
            scored = _candidate_score(req, project.packages[0], project=project, platform="pc")
            graph = build_graph((req,), {"inventory": [scored]}, list(project.packages), project, "pc")
            edge = next(e for e in graph["edges"] if e["relation"] == "requires")
            self.assertEqual(edge["version_status"], "satisfied")
            self.assertEqual(edge["availability"], "installed_in_project")

    def test_custom_or_builtin_pipeline_does_not_claim_verified_compatibility(self):
        candidate = Candidate(id="asset_store:10", source="asset_store", external_id="10", title="Example", url="", metadata={"asset_store_details": {"original_unity_version": "6000.0.0", "render_pipeline_compatibility": [{"unity_version": "6000.0.0", "compatible_pipelines": ["built_in"]}]}})
        project = ProjectSnapshot(path="test", name="test", unity_version="6000.0.1", render_pipeline="built-in-or-custom", input_backend="unknown", packages=())
        self.assertEqual(assess_candidate_compatibility(candidate, project=project, platform="pc")["status"], "unknown")
        project = ProjectSnapshot(path="test", name="test", unity_version="6000.0.1", render_pipeline="urp", input_backend="unknown", packages=(), render_pipeline_evidence="package_presence_only")
        result = assess_candidate_compatibility(candidate, project=project, platform="pc")
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(any("推定" in reason for reason in result["reasons"]))

    def test_transitive_shared_dependencies_and_version_limit_are_not_integration_proof(self):
        a, b = asset(1, "2.0.0"), asset(2, "1.0.0")
        req = GameRequirement("inventory", "Inventory", "high", "inventory", "")
        project = ProjectSnapshot(path="test", name="test", unity_version="6000.0.0f1", render_pipeline="urp", input_backend="both", packages=(), package_lock={
            "com.example.shared": {"version": "1.5.0", "dependencies": {"com.example.transitive": "1.0.0"}},
            "com.example.transitive": {"version": "1.0.0", "dependencies": {"com.example.shared": "1.0.0"}},
        })
        scored = [_candidate_score(req, c, project=None, platform="quest") for c in (a, b)]
        graph = build_graph((req,), {"inventory": scored}, [a, b], project, "quest")
        review = plan_dependency_review(graph, {a.id, b.id})
        self.assertEqual(review["integration_status"], "unverified")
        self.assertTrue(any(e["root"] == a.id and e["to"] == "upm:com.example.shared" and e["version_status"] == "below_minimum" for e in review["dependencies"]))
        self.assertTrue(any(e["to"] == "upm:com.example.transitive" and len(e["path"]) == 3 for e in review["dependencies"]))
        self.assertTrue(any(e["cycle"] for e in review["dependencies"]))
        self.assertTrue(any(s["dependency"] == "upm:com.example.shared" and s["status"] == "below_minimum" for s in review["shared_dependencies"]))
        self.assertTrue(any(e["relation"] == "shares_dependency" and e["status"] == "integration_unverified" for e in graph["edges"]))
        self.assertEqual(version_status("1.0.0-preview.1", "1.0.0"), "unknown")
        self.assertEqual(version_status("1.2.0", "1.3.0"), "satisfied")

    def test_missing_metadata_is_not_imagined_to_be_a_dependency(self):
        req = GameRequirement("inventory", "Inventory", "high", "inventory", "")
        candidate = Candidate(id="asset_store:9", source="asset_store", external_id="9", title="Inventory", url="", categories=("inventory",))
        graph = build_graph((req,), {"inventory": [_candidate_score(req, candidate, project=None, platform="pc")]}, [candidate], None, "pc")
        self.assertEqual(plan_dependency_review(graph, {candidate.id})["dependencies"], [])
        self.assertEqual(plan_dependency_review(graph, {candidate.id})["integration_status"], "unverified")

    def test_malformed_optional_dependency_data_is_ignored(self):
        req = GameRequirement("inventory", "Inventory", "high", "inventory", "")
        candidate = Candidate(id="asset_store:12", source="asset_store", external_id="12", title="Inventory", url="", categories=("inventory",), metadata={"asset_store_details": {"dependencies": None, "dependency_ids": None}, "local_validation": {"package_inspection": {"package_manifests": {"unexpected": True}}}})
        graph = build_graph((req,), {"inventory": [_candidate_score(req, candidate, project=None, platform="pc")]}, [candidate], None, "pc")
        self.assertFalse(any(e["relation"] == "requires" for e in graph["edges"]))

    def test_user_art_tags_are_editable_survive_sync_and_are_used_by_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = StackRepository(Path(directory) / "db.sqlite")
            candidate = Candidate(id="asset_store:8", source="asset_store", external_id="8", title="Forest Environment", url="", categories=("visual_assets",), ownership="owned")
            repo.upsert_candidates((candidate,))
            app = GameStackApplication(repository=repo)
            updated = app.set_art_styles({"candidate_id": candidate.id, "styles": ["リアル", "水彩"]})["candidate"]
            self.assertEqual(updated["art_styles"]["user"], ["リアル", "水彩"])
            repo.upsert_candidates((candidate,))
            self.assertEqual(candidate_styles(repo.get_candidate(candidate.id))["user"], ["リアル", "水彩"])
            self.assertEqual(repo.list_candidates(query="水彩")[0].id, candidate.id)
            result = GameStackPlanner(repo).recommend(prompt="リアルな森", remote=False)
            chosen = result["recommendations"]["visual_assets"][0]
            self.assertTrue(any("画風" in r for r in chosen["reasons"]))
            self.assertTrue(any(e["to"] == "art_style:realistic" and e["basis"] == "user_annotation" for e in result["planning_graph"]["edges"]))
            app.set_art_styles({"candidate_id": candidate.id, "styles": []})
            self.assertEqual(repo.get_candidate(candidate.id).metadata["user_art_styles"], [])
            with self.assertRaises(ApiError):
                app.set_art_styles({"candidate_id": candidate.id, "styles": ["x" * 61]})
            repo.close()


if __name__ == "__main__":
    unittest.main()
