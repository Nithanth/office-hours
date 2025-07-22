# new file
"""Evaluate extraction quality against a small gold set.

Gold file format (JSON list):
[
  {
    "url": "https://arxiv.org/pdf/1234.5678.pdf",
    "must_contain": ["Introduction", "Conclusion", "Figure 1"],
    "min_chars": 10000
  },
  ...
]
Run:
    python scripts/extract_eval.py tests/gold_extract.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure src on path
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from loguru import logger

from services.paper.text_extractor import FullTextExtractor  # type: ignore


aextractor = FullTextExtractor()


async def _eval_case(case: dict) -> bool:
    url = case["url"]
    must = case.get("must_contain", [])
    min_chars = case.get("min_chars", 0)

    txt_path = await aextractor.get_paper_content_from_id(url)  # type: ignore[arg-type]
    if not txt_path:
        logger.error("no_extract", url=url)
        return False
    text = Path(txt_path).read_text(encoding="utf-8")

    ok = True
    if len(text) < min_chars:
        logger.warning("too_short", url=url, got=len(text), want=min_chars)
        ok = False
    lower = text.lower()
    for kw in must:
        if kw.lower() not in lower:
            logger.warning("missing_kw", url=url, kw=kw)
            ok = False
    logger.info("extract_case", url=url, passed=ok)
    return ok


async def _run(path: Path) -> None:
    cases = json.loads(path.read_text())
    results = await asyncio.gather(*(_eval_case(c) for c in cases))
    passed = sum(results)
    print(f"Passed {passed}/{len(cases)} cases")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("gold_json", type=Path)
    args = ap.parse_args()

    asyncio.run(_run(args.gold_json))
