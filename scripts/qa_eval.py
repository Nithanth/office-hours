# new file
import argparse
import json
import os
from pathlib import Path
from typing import List

from loguru import logger

from services.paper.embedding import embed_query
from services.paper import vector_store as _vs


def _load_cases(case_dir: Path) -> List[dict]:
    cases: List[dict] = []
    for p in case_dir.glob("*.json"):  # simple JSON cases
        try:
            cases.append(json.loads(p.read_text()))
        except Exception as exc:
            logger.warning("case_load_failed", file=str(p), err=str(exc))
    return cases


def run_case(case: dict) -> bool:
    paper_id = case["paper_id"]
    question = case["question"]
    must_contain = case.get("must_contain", [])

    if not _vs.index_exists(paper_id):
        logger.warning("index_missing", paper_id=paper_id)
        return False

    vec = embed_query(question)
    hits = _vs.search(paper_id, vec, query_text=question, top_k=12)

    answer_ctx = "\n".join(h["text"] for h in hits)
    passed = all(k.lower() in answer_ctx.lower() for k in must_contain)
    logger.info(
        "qa_case",
        paper_id=paper_id,
        q=question,
        passed=passed,
        hits=len(hits),
    )
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run simple QA eval cases.")
    parser.add_argument("case_dir", type=Path, help="Directory containing *.json cases")
    args = parser.parse_args()

    cases = _load_cases(args.case_dir)
    passed = sum(run_case(c) for c in cases)
    total = len(cases)
    print(f"Passed {passed}/{total} (\u2713={passed}, x={total - passed})")
