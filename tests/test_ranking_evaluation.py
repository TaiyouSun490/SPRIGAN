import json
from pathlib import Path
import pytest

from game_stack_planner.evaluate_retrieval import evaluate
from game_stack_planner.ranking_evaluation import ranking_metrics


def test_metrics_rank_grades_duplicate_results_and_boundary_cases():
    result = ranking_metrics(["a", "a", "unjudged", "b"], {"a": 3, "b": 1, "c": 0}, k=3)
    assert result["returned_at_k"] == 3
    assert result["judged_at_k"] == 2
    assert result["precision_at_k"] == pytest.approx(2 / 3)
    assert result["recall_at_k"] == 1
    assert 0 < result["ndcg_at_k"] < 1
    assert ranking_metrics([], {}, k=3)["ndcg_at_k"] is None
    assert ranking_metrics([], {}, k=3)["precision_returned"] is None
    with pytest.raises(ValueError):
        ranking_metrics([], {}, k=0)
    with pytest.raises(ValueError):
        ranking_metrics([], {"a": 4})


def test_labeled_fixture_runs_offline_against_both_policies():
    data = json.loads((Path(__file__).parent / "fixtures" / "planner_relevance.json").read_text(encoding="utf-8-sig"))
    for baseline in (False, True):
        report = evaluate(data, k=3, baseline=baseline)
        assert len(report["cases"]) == 5
        assert all(case["ranked_ids"] for case in report["cases"])
        assert report["mean"]["ndcg_at_k"] > .8
        forest = next(case for case in report["cases"] if case["id"] == "forest-realistic")
        assert "local:city" not in forest["ranked_ids"]
        assert "not evidence" in report["limitations"]
