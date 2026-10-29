"""Evaluate a labeled local JSON fixture without network or the user's catalog.

Run: python -m game_stack_planner.evaluate_retrieval tests/fixtures/planner_relevance.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .models import Candidate
from .ranking_evaluation import ranking_metrics
from .repository import StackRepository
from .service import GameStackPlanner, PlanningPolicy


def evaluate(data: dict, *, k: int = 5, baseline: bool = False) -> dict:
    candidates = []
    for value in data["candidates"]:
        identifier = str(value["id"])
        candidates.append(Candidate(id=identifier, source="local", external_id=identifier, title=str(value["title"]),
                                    url="", categories=tuple(value.get("categories") or ()),
                                    description=str(value.get("description") or ""), metadata=value.get("metadata") or {}))
    policy = PlanningPolicy(alternative_diversity_penalty=0, dependency_version_cost=0, dependency_incompatible_cost=0) if baseline else PlanningPolicy()
    results = []
    with tempfile.TemporaryDirectory(prefix="sprigan-eval-") as directory:
        repository = StackRepository(Path(directory) / "evaluation.sqlite3")
        try:
            repository.upsert_candidates(candidates)
            planner = GameStackPlanner(repository, planning_policy=policy)
            for case in data["cases"]:
                result = planner.recommend(prompt=case["prompt"], platform=case.get("platform", "pc"), remote=False)
                ranking = [item["candidate"]["id"] for item in result["recommendations"].get(case["requirement"], [])]
                results.append({"id": case["id"], "ranked_ids": ranking[:k], **ranking_metrics(ranking, case["judgments"], k=k)})
        finally:
            repository.close()
    averages = {}
    for key in ("precision_at_k", "precision_returned", "recall_at_k", "ndcg_at_k"):
        values = [case[key] for case in results if case[key] is not None]
        averages[key] = sum(values) / len(values) if values else None
    return {"fixture": data.get("description", ""), "mode": "baseline_no_diversity_or_dependency_cost" if baseline else "default", "k": k,
            "cases": results, "mean": averages, "limitations": "Synthetic labels; unjudged results are counted as zero gain; not evidence of production quality."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
    print(json.dumps(evaluate(data, k=args.k, baseline=args.baseline), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
