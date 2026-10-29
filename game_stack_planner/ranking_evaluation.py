"""Offline ranking evaluation with explicit graded relevance labels."""
from __future__ import annotations

import math
from typing import Any


def ranking_metrics(ranked_ids: list[str], judgments: dict[str, int], *, k: int = 5) -> dict[str, Any]:
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError("k must be a positive integer")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 3 for value in judgments.values()):
        raise ValueError("relevance grades must be integers 0 through 3")
    # Repeated IDs must not receive duplicate credit.
    ranking = list(dict.fromkeys(ranked_ids))[:k]
    relevant = {key for key, value in judgments.items() if value > 0}
    hits = sum(judgments.get(key, 0) > 0 for key in ranking)
    gain = sum((2 ** judgments.get(key, 0) - 1) / math.log2(index + 2) for index, key in enumerate(ranking))
    ideal = sorted(judgments.values(), reverse=True)[:k]
    ideal_gain = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
    return {"precision_at_k": hits / k, "precision_returned": hits / len(ranking) if ranking else None,
            "recall_at_k": hits / len(relevant) if relevant else None,
            "ndcg_at_k": gain / ideal_gain if ideal_gain else None,
            "judged_at_k": sum(key in judgments for key in ranking), "returned_at_k": len(ranking)}
