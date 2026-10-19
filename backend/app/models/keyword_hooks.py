"""Keeps the keyword index in step with the data without touching every API: after each flush, records of the indexed
types that were added / changed / removed through the ORM get their index rows rewritten in the same transaction.
(Bulk deletes - re-processing a document, the nightly program purge - leave orphan rows; the search ignores rows whose
record is gone and the nightly rebuild removes them.)"""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@event.listens_for(Session, "after_flush")
def _keyword_index_after_flush(session: Session, flush_context) -> None:
    from app.models import Chunk, Document, Program, QAPair, RecordKeywords, Variable
    tracked = (Chunk, QAPair, Variable, Program, RecordKeywords)
    changed = [o for o in (*session.new, *session.dirty) if isinstance(o, tracked)]
    removed = [o for o in session.deleted if isinstance(o, tracked)]
    docs_removed = [o for o in session.deleted if isinstance(o, Document)]
    if not (changed or removed or docs_removed):
        return
    from app.rag.keyword_index import apply_changes
    conn = session.connection()
    try:
        with conn.begin_nested():  # a SAVEPOINT: a failed index write rolls back only itself, never the data being saved
            apply_changes(conn, changed, removed, docs_removed)
    except Exception:  # noqa: BLE001 - a stale index must never block saving the data itself (the nightly rebuild repairs it)
        logger.exception("keyword index update failed")
