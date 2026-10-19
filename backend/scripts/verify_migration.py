"""Migration verification: counts and checksums of the data that must survive, before / after an alembic step.

    python -m scripts.verify_migration snapshot before.json
    python -m scripts.verify_migration compare before.json      # exits 1 when anything that must be kept changed

Kept = every row of qa_pairs (question, answer, active), variables, documents, chunks, keyword_entries counts,
profiles (name/phone/consent), unanswered_questions. Q&A categories are reported separately (0025 changes them).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

from sqlalchemy import text

from app.db.session import get_engine

CHECKS = {
    "qa_pairs": "SELECT id, question, answer_template, active FROM qa_pairs ORDER BY id",
    "variables": "SELECT key, value FROM variables ORDER BY key",
    "documents": "SELECT id, title, status, chunk_count FROM documents ORDER BY id",
    "chunks": "SELECT id, document_id, content FROM chunks ORDER BY id",
    "profiles": "SELECT id, name, phone_hash, consent_given, location FROM profiles ORDER BY id",
    "unanswered_questions": "SELECT id, question FROM unanswered_questions ORDER BY id",
    "programs": "SELECT id, name FROM programs ORDER BY id",
    "record_keywords": "SELECT source_type, source_ref, added::text FROM record_keywords ORDER BY 1, 2",
}
INFO = {"qa_categories": "SELECT coalesce(category, '-'), count(*) FROM qa_pairs GROUP BY 1 ORDER BY 1",
        "keyword_entries": "SELECT source_type, count(*) FROM keyword_entries GROUP BY 1 ORDER BY 1"}


async def snapshot() -> dict:
    out = {}
    async with get_engine().connect() as conn:
        for name, sql in CHECKS.items():
            rows = (await conn.execute(text(sql))).all()
            out[name] = {"count": len(rows), "sha256": hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()}
        for name, sql in INFO.items():
            out[name] = {str(k): v for k, v in (await conn.execute(text(sql))).all()}
    return out


def main() -> None:
    cmd, path = sys.argv[1], sys.argv[2]
    now = asyncio.run(snapshot())
    if cmd == "snapshot":
        json.dump(now, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps(now, ensure_ascii=False, indent=1))
        return
    before = json.load(open(path, encoding="utf-8"))
    bad = [k for k in CHECKS if before[k] != now[k]]
    print(json.dumps({"preserved": [k for k in CHECKS if k not in bad], "changed": {k: [before[k], now[k]] for k in bad},
                      "qa_categories_before": before["qa_categories"], "qa_categories_now": now["qa_categories"],
                      "keyword_entries_now": now["keyword_entries"]}, ensure_ascii=False, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
