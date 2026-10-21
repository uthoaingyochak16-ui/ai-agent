"""A short reply to the assistant's own question ("course korte agrohi?" -> "ji", "Ok", "hya, dhakay").

Such a message is not a new request: on its own "ji" means nothing, so it must not be read as a greeting, as small
talk ("Ok" -> "Sure 🙂") or as a knowledge question. It is the answer to what the assistant just asked, and the reply
should carry the conversation forward. Detection is deliberately simple and topic-free: the assistant's last turn
asked something, and the guest's message is short and is not itself a question.
"""
from __future__ import annotations

import re

MAX_WORDS = 6
# a guest message with one of these is a new question, not an answer to ours
_ASKS = re.compile(r"(?i)\?|\b(kobe|kokhon|koto|kto|kothay|kothai|kivabe|kibhabe|keno|kno|kemne|what|when|where|how|why|which|who)\b"
                   r"|কবে|কখন|কত|কোথায়|কীভাবে|কিভাবে|কেন|কোন")
_SENTENCE_END = re.compile(r"(?<=[।.!?])\s+")


def asked_question(history: list[tuple[str, str]]) -> str | None:
    """The question in the assistant's last turn (its last sentence with a "?"), when that turn asked one."""
    if not history or history[-1][0] != "assistant":
        return None
    asks = [s.strip() for s in _SENTENCE_END.split(history[-1][1] or "") if "?" in s]
    return asks[-1][-300:] if asks else None


def reply_to_our_question(text: str, history: list[tuple[str, str]]) -> str | None:
    """The assistant's question that `text` answers, or None when `text` is a request of its own."""
    t = (text or "").strip()
    if not t or len(t.split()) > MAX_WORDS or _ASKS.search(t):
        return None
    return asked_question(history)


CONTINUE_NOTE = ("CONTINUE: the QUESTION is the guest's short answer to the question you asked in your last message. "
                 "Work out what that answer means (yes / no / a choice / a place...) and take the next helpful step from "
                 "CONTEXT - for example the details, steps or link they now need. Do not ask what they mean. "
                 "A bare acknowledgement (\"ok\", \"thik ache\", \"accha\") of what you said is not a request: reply in "
                 "one short sentence and repeat nothing you already told them. "
                 "Reply NO_ANSWER only if CONTEXT has nothing for what they now want.")
