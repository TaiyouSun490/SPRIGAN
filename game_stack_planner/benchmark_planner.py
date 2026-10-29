"""Benchmark offline planning against a read-only catalog snapshot in memory.

No remote search, embedding inference, builds, downloads, or source DB writes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from statistics import median
from time import perf_counter

from .repository import StackRepository, default_database_path
from .service import GameStackPlanner


def benchmark(database: Path, *, prompt: str, platform: str = "quest",
              budget: str = "owned_first", runs: int = 3) -> dict:
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= 20:
        raise ValueError("runs must be an integer from 1 through 20")
    repository = StackRepository(":memory:")
    try:
        # Copy only catalog evidence, not history, tokens, or embeddings. One
        # SQLite read transaction also avoids inconsistent tables during a sync.
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("PRAGMA query_only = ON")
            source.execute("BEGIN")
            for table in ("candidates", "asset_store_purchase_evidence", "asset_rag_documents"):
                rows = source.execute("SELECT * FROM " + table)
                columns = [column[0] for column in rows.description]
                repository._connection.executemany(
                    "INSERT INTO " + table + " (" + ",".join(columns) + ") VALUES ("
                    + ",".join("?" for _ in columns) + ")", rows)
        repository._connection.commit()
        planner = GameStackPlanner(repository)
        samples, signatures = [], []
        for _ in range(runs):
            started = perf_counter()
            result = planner.recommend(prompt=prompt, platform=platform, budget=budget, remote=False)
            samples.append(perf_counter() - started)
            ranking = {key: [item["candidate"]["id"] for item in items]
                       for key, items in result["recommendations"].items()}
            plans = {plan["id"]: [(item["candidate"]["id"], item["covers"]) for item in plan["selected"]]
                     for plan in result["plans"]}
            signatures.append(sha256(json.dumps([ranking, plans], sort_keys=True).encode()).hexdigest())
        diagnostics = result["planning_diagnostics"]
        return {"mode": "offline_read_only_snapshot_lexical_rag", "runs": runs,
                "seconds": [round(value, 4) for value in samples], "median_seconds": round(median(samples), 4),
                "catalog_size": diagnostics["catalog_size"],
                "candidate_requirement_pairs": diagnostics["candidate_requirement_pairs_considered"],
                "ranking_and_plan_signature": signatures[0], "stable_results": len(set(signatures)) == 1,
                "limitations": "Includes in-memory history writes; excludes source snapshot preparation, network and dense model inference."}
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=default_database_path())
    parser.add_argument("--prompt", default="森で動物を追跡するVR体験。オンライン不要。")
    parser.add_argument("--platform", choices=("pc", "mobile", "webgl", "vr", "quest"), default="quest")
    parser.add_argument("--budget", choices=("free", "mixed", "owned_first"), default="owned_first")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.database, prompt=args.prompt, platform=args.platform,
                               budget=args.budget, runs=args.runs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
