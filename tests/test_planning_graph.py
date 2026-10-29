from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from game_stack_planner.models import Candidate, GameRequirement, ProjectSnapshot
from game_stack_planner.planning_graph import CandidateIndex, build_graph
from game_stack_planner.repository import StackRepository
from game_stack_planner.requirements import derive_requirements, excluded_requirements
from game_stack_planner.service import GameStackPlanner, PlanningPolicy, _candidate_score, _make_plan, _tokens


def req(key):
    return GameRequirement(key, key, "high", "unity " + key, "test")


def option(identifier, score, *, ownership="owned"):
    return {"candidate": {"id": identifier, "title": identifier, "source": "asset_store", "ownership": ownership},
            "score": score, "reasons": ["test evidence"], "risks": [], "installed_in_project": False}


class PlanningGraphTests(unittest.TestCase):
    def test_whole_plan_reuses_only_independently_supported_capabilities(self):
        requirements = (req("inventory"), req("save_system"))
        shared_a, shared_b = option("asset_store:1", 76), option("asset_store:1", 76)
        candidates = {"inventory": [option("asset_store:2", 80), shared_a],
                      "save_system": [option("asset_store:3", 80), shared_b]}
        plan = _make_plan("installed_first", requirements, candidates)
        self.assertEqual(len(plan["selected"]), 1)
        self.assertEqual(plan["selected"][0]["candidate"]["id"], "asset_store:1")
        self.assertEqual(set(plan["selected"][0]["covers"]), {"inventory", "save_system"})
        self.assertEqual(set(plan["selected"][0]["coverage_evidence"]), {"inventory", "save_system"})
        self.assertEqual(plan["coverage"], 100)
        # One high-scoring edge must not create a second unsupported capability.
        plan = _make_plan("installed_first", requirements, {"inventory": [shared_a]})
        self.assertIn("save_system", plan["missing"])

    def test_low_quality_shared_tool_does_not_displace_clear_fits(self):
        requirements = (req("inventory"), req("save_system"))
        plan = _make_plan("low_risk", requirements, {
            "inventory": [option("shared", 30), option("best_inventory", 95)],
            "save_system": [option("shared", 30), option("best_save", 95)],
        })
        self.assertEqual({v["candidate"]["id"] for v in plan["selected"]}, {"best_inventory", "best_save"})

    def test_graph_records_only_observed_dependencies_and_unknowns(self):
        main = Candidate(id="asset_store:1", source="asset_store", external_id="1", title="Inventory",
                         url="", ownership="owned", categories=("inventory",),
                         metadata={"asset_store_details": {"source_url": "https://example.com/1",
                           "dependencies": [{"product_id": "2", "title": "Other package"}]}})
        dependency = Candidate(id="asset_store:2", source="asset_store", external_id="2", title="Other",
                               url="", ownership="owned")
        requirement = req("inventory")
        scored = _candidate_score(requirement, main, project=None, platform="quest")
        graph = build_graph((requirement,), {"inventory": [scored]}, [main, dependency], None, "quest")
        edges = [v for v in graph["edges"] if v["relation"] == "requires"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["availability"], "owned_not_confirmed_installed")
        plan = _make_plan("installed_first", (requirement,), {"inventory": [scored]}, graph=graph)
        self.assertEqual(plan["unresolved_dependencies"][0]["to"], "asset_store:2")
        project = ProjectSnapshot(path="test", name="test", unity_version=None, render_pipeline="urp",
                                  input_backend="input-system", packages=(dependency,))
        graph = build_graph((requirement,), {"inventory": [scored]}, [main, dependency], project, "quest")
        self.assertEqual(next(v for v in graph["edges"] if v["relation"] == "requires")["availability"], "installed_in_project")

    def test_inverted_index_keeps_every_candidate_the_original_gate_could_accept(self):
        requirement = GameRequirement("inventory", "inventory", "high", "unity inventory item system", "test")
        candidates = [
            Candidate(id="local:category", source="local", external_id="1", title="Unknown", url="", categories=("inventory",)),
            Candidate(id="github:identity", source="github", external_id="2", title="Inventory library", url=""),
            Candidate(id="asset_store:description", source="asset_store", external_id="3", title="Thing", url="", description="inventory item"),
            Candidate(id="asset_store:dense", source="asset_store", external_id="4", title="Unrelated text", url="", ownership="owned"),
            Candidate(id="local:irrelevant", source="local", external_id="5", title="camera", url=""),
        ]
        index = CandidateIndex(candidates, _tokens)
        rag = {"asset_store:dense": {"retrieval_mode": "hybrid_dense", "score": .83}}
        expected = {c.id for c in candidates if _candidate_score(requirement, c, project=None, platform="pc", rag_match=rag.get(c.id))["score"] > 0}
        self.assertTrue(expected <= {c.id for c in index.retrieve(requirement, set(rag))})
        self.assertNotIn("local:irrelevant", {c.id for c in index.retrieve(requirement, set(rag))})

    def test_diagnostics_are_exposed_and_search_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            db = StackRepository(Path(folder) / "catalog.db")
            db.upsert_candidates(tuple(Candidate(id=f"local:{n}", source="local", external_id=str(n),
                                                  title=f"decorative object {n}", url="") for n in range(300)))
            result = GameStackPlanner(db).recommend(prompt="インベントリ", remote=False)
            self.assertEqual(result["planning_graph"]["schema"], "sprigan.planning-evidence-graph.v1")
            self.assertLess(result["planning_diagnostics"]["candidate_requirement_pairs_considered"], result["planning_diagnostics"]["full_scan_pairs"])
            self.assertTrue(all(p["selection_method"] == "bounded_graph_beam" for p in result["plans"]))
            db.close()

    def test_planning_does_not_drop_candidates_after_public_list_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            db = StackRepository(Path(folder) / "catalog.db")
            db.upsert_candidates(tuple(Candidate(id=f"local:{n:05d}", source="local", external_id=str(n),
                title=f"aaa decorative object {n}", url="") for n in range(5001)) + (
                Candidate(id="local:zzzz", source="local", external_id="zzzz", title="zzz save serializer", url=""),))
            result = GameStackPlanner(db).recommend(prompt="セーブ", remote=False)
            self.assertEqual(result["planning_diagnostics"]["catalog_size"], 5002)
            self.assertTrue(any(item["candidate"]["id"] == "local:zzzz" for item in result["recommendations"]["save_system"]))
            db.close()

    def test_planning_limits_can_be_overridden_and_are_validated(self):
        self.assertEqual(PlanningPolicy(beam_width=5).beam_width, 5)
        with self.assertRaises(ValueError):
            PlanningPolicy(beam_width=0)
        with self.assertRaises(ValueError):
            PlanningPolicy(new_asset_cost=float("nan"))

    def test_explicit_exclusions_do_not_reappear_through_archetypes_or_baseline(self):
        brief = "ホラーゲーム、オンライン不要、セーブなし"
        keys = {r.key for r in derive_requirements(brief)}
        self.assertIn("horror_atmosphere", keys)
        self.assertNotIn("networking", keys)
        self.assertNotIn("lobby_matchmaking", keys)
        self.assertNotIn("save_system", keys)
        self.assertIn("networking", excluded_requirements(brief))
        self.assertNotIn("networking", {r.key for r in derive_requirements("co-op game without multiplayer")})
        self.assertIn("networking", {r.key for r in derive_requirements("online multiplayer game")})
        self.assertIn("networking", {r.key for r in derive_requirements("オンライン不要ではない")})
        self.assertIn("save_system", {r.key for r in derive_requirements("セーブなしでは困る")})

    def test_forest_context_changes_retrieval_and_beats_unrelated_city_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            db = StackRepository(Path(folder) / "catalog.db")
            db.upsert_candidates((
                Candidate(id="local:forest", source="local", external_id="forest", title="Forest vegetation pack", url="", ownership="owned"),
                Candidate(id="local:city", source="local", external_id="city", title="City graveyard environment", url="", ownership="owned", categories=("visual_assets",)),
                Candidate(id="local:audio", source="local", external_id="audio", title="Forest soundtrack music", url="", ownership="owned"),
            ))
            result = GameStackPlanner(db).recommend(prompt="森を探索", remote=False)
            picks = result["recommendations"]["visual_assets"]
            self.assertEqual(picks[0]["candidate"]["id"], "local:forest")
            self.assertTrue(any("舞台の文脈一致" in reason for reason in picks[0]["reasons"]))
            self.assertFalse(any(item["candidate"]["id"] == "local:audio" for item in picks))
            self.assertTrue(any(edge["relation"] == "matches_context" and edge["from"] == "local:forest" for edge in result["planning_graph"]["edges"]))
            self.assertEqual(result["inferred_context"][0]["id"], "forest")
            searched = {item["requirement"] for item in result["asset_store_searches"]}
            self.assertEqual(searched, set(result["plans"][0]["missing"]))
            db.close()


if __name__ == "__main__":
    unittest.main()
