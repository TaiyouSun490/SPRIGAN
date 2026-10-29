from __future__ import annotations

from game_stack_planner.graph_reasoning import dependency_issues, diversify_candidates
from game_stack_planner.models import GameRequirement
from game_stack_planner.service import PlanningPolicy, _make_plan, _tokens
from game_stack_planner.service import GameStackPlanner
from game_stack_planner.repository import StackRepository
from game_stack_planner.models import Candidate
import tempfile
from pathlib import Path


def item(identifier, score, title=None):
    return {"candidate": {"id": identifier, "title": title or identifier, "source": "asset_store", "ownership": "owned"}, "score": score, "reasons": [], "risks": [], "installed_in_project": False}


def test_graph_cost_changes_choice_only_when_explicit_issue_exists():
    req = GameRequirement("inventory", "inventory", "high", "inventory", "")
    recommendations = {"inventory": [item("bad", 95), item("safe", 85)]}
    graph = {"nodes": [], "edges": [
        {"from": "bad", "to": "requirement:inventory", "relation": "may_support"},
        {"from": "safe", "to": "requirement:inventory", "relation": "may_support"},
        {"from": "bad", "to": "shared", "relation": "requires", "basis": "unity_packages_lock", "version_status": "below_minimum", "required_version": "2.0.0", "resolved_version": "1.0.0", "availability": "installed_in_project"},
    ]}
    selected = _make_plan("low_risk", (req,), recommendations, graph=graph)
    assert selected["selected"][0]["candidate"]["id"] == "safe"
    costs = dependency_issues(graph, {"bad", "safe"}, version_cost=20, incompatible_cost=45)
    assert costs["bad"][("shared", "below_minimum")]["path"] == ["bad", "shared"]
    graph["edges"][2]["version_status"] = "unknown"
    selected = _make_plan("low_risk", (req,), recommendations, graph=graph)
    assert selected["selected"][0]["candidate"]["id"] == "bad"
    assert selected["planning_dependency_issues"] == []


def test_transitive_cost_is_counted_once_for_shared_dependency():
    reqs = tuple(GameRequirement(key, key, "high", key, "") for key in ("one", "two"))
    graph = {"nodes": [], "edges": [
        {"from": "a", "to": "requirement:one", "relation": "may_support"},
        {"from": "b", "to": "requirement:two", "relation": "may_support"},
        {"from": "a", "to": "bridge", "relation": "requires"},
        {"from": "b", "to": "bridge", "relation": "requires"},
        {"from": "bridge", "to": "shared", "relation": "requires", "basis": "unity_packages_lock", "version_status": "below_minimum", "required_version": "2.0.0", "resolved_version": "1.0.0"},
    ]}
    plan = _make_plan("low_risk", reqs, {"one": [item("a", 90)], "two": [item("b", 90)]}, graph=graph)
    assert len(plan["selected"]) == 2
    assert len(plan["planning_dependency_issues"]) == 1
    assert plan["planning_dependency_issues"][0]["candidate_ids"] == ["a", "b"]
    assert plan["planning_dependency_issues"][0]["path"] == ["a", "bridge", "shared"]


def test_dependency_cost_is_used_before_option_cutoff():
    req = GameRequirement("inventory", "inventory", "high", "inventory", "")
    choices = [item(f"bad-{i}", 95 - i) for i in range(8)] + [item("safe", 85)]
    graph = {"nodes": [], "edges": [{"from": i["candidate"]["id"], "to": "requirement:inventory", "relation": "may_support"} for i in choices]}
    graph["edges"] += [{"from": f"bad-{i}", "to": f"dep-{i}", "relation": "requires", "version_status": "below_minimum"} for i in range(8)]
    plan = _make_plan("low_risk", (req,), {"inventory": choices}, graph=graph)
    assert plan["selected"][0]["candidate"]["id"] == "safe"


def test_service_finds_safer_candidate_beyond_initial_display_cutoff():
    with tempfile.TemporaryDirectory() as directory:
        repository = StackRepository(Path(directory) / "catalog.db")
        try:
            unsafe = tuple(Candidate(id=f"asset_store:{100 + i}", source="asset_store", external_id=str(100 + i), title=f"Inventory suite {i:02}", url="", ownership="owned", categories=("inventory",), metadata={"asset_store_details": {"dependencies": [{"product_id": "999", "title": "PC only"}]}}) for i in range(8))
            safe = Candidate(id="asset_store:200", source="asset_store", external_id="200", title="Inventory suite 99", url="", ownership="owned", categories=("inventory",))
            dependency = Candidate(id="asset_store:999", source="asset_store", external_id="999", title="PC only", url="", metadata={"asset_store_details": {"platforms": ["pc"]}})
            repository.upsert_candidates((*unsafe, safe, dependency))
            result = GameStackPlanner(repository).recommend(prompt="インベントリ", platform="quest", remote=False)
            assert any(item["candidate"]["id"] == safe.id for item in result["recommendations"]["inventory"])
            assert any(safe.id == item["candidate"]["id"] for item in result["plans"][0]["selected"])
        finally:
            repository.close()


def test_duplicate_diversity_preserves_first_and_does_not_promote_weak_candidate():
    options = [item("1", 100, "Forest Plants Pack Volume Alpha"), item("2", 99, "Forest Plants Pack Volume Beta"), item("3", 96, "Alpine Landscape Vegetation"), item("4", 60, "Animal Controller")]
    result = diversify_candidates(options, 4, _tokens, penalty=8)
    assert [value["candidate"]["id"] for value in result] == ["1", "3", "2", "4"]
    assert [value["score"] for value in result] == [100, 96, 99, 60]
    assert PlanningPolicy(alternative_diversity_penalty=0).alternative_diversity_penalty == 0
