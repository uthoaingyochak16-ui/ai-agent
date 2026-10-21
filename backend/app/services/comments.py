"""AI replies to comments on the Facebook Page's posts, and moderation of spam and attacks on the organisation.

webhook `feed` (or a staff "scan" of recent posts) -> post_comments row (dedup on comment_id) -> process_comment
(worker, after comment_reply_delay_seconds):
  1. one cheap AI call ("router" route) reads the post + comment: what kind of comment it is (a real question, thanks,
     a complaint, spam, abuse, or an attack on Quantum Method / Quantum Foundation), why (one sentence for staff), how
     sure it is, its language, the comment as a self-contained question, and a short reply for thanks / greetings;
  2. a question is answered by the normal knowledge pipeline in PUBLIC COMMENT mode: short, nothing personal, invites
     to the inbox when it needs more;
  3. the plan is carried out (like / reply / hide / delete / tell staff), or kept as a draft for staff (comment_mode
     "review", the hourly cap, a scanned old comment, or a removal the AI is not sure enough about).
Spam / abuse: comment_spam_action (ignore | hide | delete). Against the organisation: comment_against_action
(notify | hide | delete); staff are always told (alert webhook + "Needs staff"), and nothing is removed below
comment_remove_min_confidence. Hidden comments can be shown again from the Post comments page; deleted ones cannot.
Threads: a reply inside a thread is answered only when the person who wrote the thread's first comment continues it
after our reply (at most comment_thread_max_replies times); talk between other people is only moderated.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels import page_comments as pc
from app.channels.graph import GraphError
from app.llm.service import LLMService, LLMUnavailable
from app.models import PostComment
from app.privacy.guard import PrivacyGuard
from app.rag.search import shorten
from app.services.language import detect, localized
from app.services.outbound import ChannelNotConfigured, get_credentials
from app.services.post_reply import parse_json_object
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
MODES = ("off", "review", "auto")
KINDS = ("question", "praise", "greeting", "reaction", "complaint", "against", "spam", "abuse", "other")
REMOVABLE = ("spam", "abuse", "against")
CHANNEL = "messenger"   # a Facebook Page's token and settings live on the Messenger channel account of the same Page


def chan_of(c) -> str:
    """The channel account whose token acts on this comment: Messenger (Facebook Page) or Instagram."""
    return "instagram" if getattr(c, "channel", "facebook") == "instagram" else CHANNEL


def ig(c) -> bool:
    return getattr(c, "channel", "facebook") == "instagram"
ANSWERED = ("llm", "llm_large", "qa_shortcut", "cache", "variable", "program", "kb_extract")

CLASSIFY_SYSTEM = """You read one comment that someone wrote under a Facebook post of Quantum Foundation (a non-profit in
Bangladesh that runs the Quantum Method meditation courses, programs, blood donation, Quantum Lab ...). Comments are in
Bangla, Banglish or English. Return ONLY a JSON object:
{"kind": "question"|"praise"|"greeting"|"reaction"|"complaint"|"against"|"spam"|"abuse"|"other",
 "reason": string, "confidence": number, "language": "bn"|"en", "question": string, "reply": string}
kind:
- question: a real person asks something or wants something from the organisation: information, how to join, when,
  where, the fee, "details?", "interested", "info din", "inbox please", "ami jete chai", a request for prayer or help.
- praise: thanks, appreciation, prayers ("Alhamdulillah", "MashaAllah", "Ameen", "khub valo laglo"), agreement, a good
  experience they share.
- greeting: only a salam / hello.
- reaction: only emoji, a sticker, tags of other people, "nice", "done", "👍", a single word with no request.
- complaint: the person's OWN problem or bad experience with the organisation that they want solved or heard ("call
  dhore na", "course e taka diye seat pai nai") - criticism that still talks to us, even when it is angry.
- against: an attack on Quantum Method, Quantum Foundation, its founder or its people aimed at other readers, not at
  getting help: calling it a cult, fraud, business, "haram", "shirk", "bhondo", "dalali", accusations without a
  personal case, mockery, false claims, propaganda, telling people to stay away or boycott, insulting the founder.
  A polite question about a controversy ("eta ki islam e jayej?") is a question, not against.
- spam: not from someone engaging with the post: ads, selling, "earn money", links or phone numbers to other
  businesses, page/ID promotion, loan / crypto / betting offers, scams, repeated copy-paste text, unrelated content.
- abuse: insults, hate, vulgar or threatening words (towards anyone).
- other: anything else (an unrelated remark, a debate between other commenters).
reason: one short sentence in English for the staff: why you chose this kind (quote the telling words).
confidence: 0.0-1.0, how sure you are of the kind.
language: "en" when the comment is in English, otherwise "bn" (Bangla or Banglish).
question: for kind question only, the comment rewritten as one self-contained question in the comment's own language,
  using the POST (and the thread) for what it leaves out ("kobe?" under a post about Songhayon -> "Songhayon kobe
  hobe?"); otherwise "".
reply: for kind praise or greeting only, one short warm reply of at most 15 words, in Bangla script when language is bn
  (never Banglish), in English when en; polite ("আপনি"), no question, at most one emoji; otherwise ""."""

COMMENT_RULES = """
PUBLIC COMMENT MODE (this overrides the chat rules above about length, greetings and offering a team member):
You are replying publicly under the organisation's own Facebook post; everyone can read the reply.
THE POST: «{post}»
- Answer the comment in 1 or 2 short sentences (about {chars} characters at most), in the language REPLY IN gives.
- Never ask for or mention personal details (phone, address, age, illness ...), never repeat anything private.
- When the answer needs personal help or more details than fit in a comment, give the short answer and invite them to
  send the Page a message (inbox) for details.
- Do not start with a greeting or the person's name; no hashtags; at most one emoji.
- Never tell them to type "agent"."""

_AGENT_HINT = re.compile(r"[^।.!?\n]*[\"“']agent[\"”'][^।.!?\n]*[।.!?]?\s*", re.IGNORECASE)
_ONLY_EMOJI = re.compile(r"^[\W_\d\s]*$")
_LINK = re.compile(r"(?i)https?://|www\.|\bbit\.ly/|\bwa\.me/|t\.me/")


async def is_on(session: AsyncSession) -> bool:
    return (await get_setting(session, "comment_mode") or "off") != "off"


# ---- the webhook side --------------------------------------------------------------------------------------------
async def store(session: AsyncSession, events: list[pc.CommentEvent], source: str = "webhook") -> list[int]:
    """Durable insert, deduplicated on the comment id. Returns the new rows' ids (to enqueue)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    ids: list[int] = []
    for e in events:
        row = await session.execute(pg_insert(PostComment).values(
            page_id=e.page_id, post_id=e.post_id, comment_id=e.comment_id, parent_id=e.parent_id, from_id=e.from_id,
            from_name=(e.from_name or "")[:160] or None, message=e.message[:5000], source=source, post_text=e.post_text,
            channel=e.channel, post_url=e.post_url)
            .on_conflict_do_nothing(index_elements=["comment_id"]).returning(PostComment.id))
        if (got := row.first()) is not None:
            ids.append(got[0])
    return ids


async def enqueue(queue, comment_row_id: int, delay_s: float, tag: str = "") -> None:
    if queue is None:
        return
    try:
        await queue.enqueue_job("process_comment_job", comment_row_id, _job_id=f"cmt-{comment_row_id}{tag}",
                                _defer_by=timedelta(seconds=max(0.0, delay_s)))
    except Exception:  # noqa: BLE001 - the row is durable; requeue_stale picks it up
        logger.exception("enqueue failed for comment %s", comment_row_id)


async def scan(session: AsyncSession, page_id: str | None, posts: int = 10) -> dict:
    """Read the comments of the latest posts of every connected Facebook Page and Instagram account (comments written
    before the webhook was on, or missed). New ones are stored with source "scan": moderated as usual, but never
    answered or liked on their own. page_id: one Page / account only."""
    from app.models import ChannelConfig
    cfgs = (await session.execute(select(ChannelConfig).where(ChannelConfig.channel.in_((CHANNEL, "instagram")),
                                                              ChannelConfig.account_id.is_not(None)))).scalars().all()
    cfgs = [c for c in cfgs if c.access_token_enc and (not page_id or c.account_id == page_id)]
    if not cfgs:
        raise ChannelNotConfigured(CHANNEL)
    found: list[pc.CommentEvent] = []
    errors: list[str] = []
    for cfg in cfgs:
        creds = await get_credentials(session, cfg.channel, account_id=cfg.account_id, for_sending=False)
        try:
            found += await (pc.ig_recent_comments if cfg.channel == "instagram" else pc.recent_comments)(cfg.account_id, creds, posts=posts)
        except GraphError as exc:   # one account failing must not hide the others' comments
            errors.append(f"{cfg.label or cfg.account_id}: {exc.message}")
    if errors and not found:
        raise GraphError(400, None, None, "; ".join(errors))
    ids = await store(session, found, source="scan")
    return {"posts": posts, "accounts": len(cfgs), "comments": len(found), "new": len(ids), "ids": ids, "errors": errors}


# ---- the worker side ---------------------------------------------------------------------------------------------
async def process_comment(maker: async_sessionmaker[AsyncSession], row_id: int, *, llm: LLMService | None = None,
                          embedder=None) -> str:
    async with maker() as session:
        c = (await session.execute(select(PostComment).where(PostComment.id == row_id)
                                   .with_for_update(skip_locked=True))).scalar_one_or_none()
        if c is None or c.status != "queued":
            return "skipped"
        c.status, c.attempts = "processing", c.attempts + 1
        await session.commit()
        try:
            await _handle(session, c, llm or LLMService(async_sessionmaker(session.bind, expire_on_commit=False)), embedder)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            c = await session.get(PostComment, row_id)
            c.status = "failed" if c.attempts >= MAX_ATTEMPTS else "queued"
            c.error = f"{type(exc).__name__}: {exc}"[:500]
            logger.exception("comment %s failed", row_id)
        await session.commit()
        return c.status


async def _handle(session: AsyncSession, c: PostComment, llm: LLMService, embedder) -> None:
    mode = await get_setting(session, "comment_mode") or "off"
    if mode == "off" and c.source != "scan":   # a scan is a staff request: it runs whatever the mode
        c.status, c.error = "ignored", "comment replies are switched off"
        return
    try:
        read = await get_credentials(session, chan_of(c), account_id=c.page_id, for_sending=False)
    except ChannelNotConfigured:
        c.status, c.error = "ignored", f"no Page token for page {c.page_id} (Channels page)"
        return
    if c.post_text is None:
        c.post_text = await _post_text(session, c, read)
    thread = await _thread(session, c)
    await plan(session, llm, c, thread or [], embedder, answer=thread is not None)
    if thread is None and c.action is None:
        c.status, c.kind = "ignored", c.kind if c.kind in REMOVABLE else "thread"
        return
    if await _waits_for_staff(session, c, mode):
        c.status = "needs_staff" if c.kind == "against" else "draft"
        if c.kind == "against":
            await _notify(session, c)
        return
    try:
        send = await get_credentials(session, chan_of(c), account_id=c.page_id)
    except ChannelNotConfigured:  # replies are switched off on the Channels page: keep it for staff
        c.status, c.error = "draft", "replies are switched off for this Page (Channels page)"
        return
    await publish(session, c, send)


async def _waits_for_staff(session: AsyncSession, c: PostComment, mode: str) -> bool:
    """Anything public with words or a removal waits for a person in review mode, at the hourly cap, for a scanned
    old comment, and when the AI is not sure enough to remove. A like alone goes ahead (not for scanned comments)."""
    if c.action in ("hide", "delete") and (c.confidence or 0) < float(await get_setting(session, "comment_remove_min_confidence") or 0):
        c.error = f"the AI suggests to {c.action} it but is not sure enough to do it on its own: please check"
        return True
    public = bool(c.reply_text) or c.action in ("hide", "delete")
    if c.source == "scan":
        return public or c.kind == "against"
    return public and (mode == "review" or (bool(c.reply_text) and await _over_cap(session)))


async def _post_text(session: AsyncSession, c: PostComment, creds) -> str:
    known = (await session.execute(select(PostComment.post_text).where(
        PostComment.post_id == c.post_id, PostComment.post_text.is_not(None)).limit(1))).scalar_one_or_none()
    if known is not None:
        return known
    try:
        if ig(c):
            text, c.post_url = await pc.ig_post(c.post_id, creds)
            return text[:3000]
        return (await pc.post_text(c.post_id, creds))[:3000]
    except GraphError as exc:  # the reply can still be written from the comment alone
        logger.info("post %s text not readable: %s", c.post_id, exc)
        return ""


async def _thread(session: AsyncSession, c: PostComment) -> list[tuple[str, str]] | None:
    """[] for a top-level comment; the earlier turns for a thread we take part in; None = not ours to answer."""
    if not c.parent_id or c.parent_id == c.post_id:
        return []
    first = (await session.execute(select(PostComment).where(PostComment.comment_id == c.parent_id))).scalar_one_or_none()
    if first is None or not first.reply_text or first.from_id != c.from_id:
        return None
    earlier = (await session.execute(select(PostComment).where(
        PostComment.parent_id == c.parent_id, PostComment.from_id == c.from_id, PostComment.id < c.id,
        PostComment.reply_text.is_not(None)).order_by(PostComment.id))).scalars().all()
    if len(earlier) + 1 > int(await get_setting(session, "comment_thread_max_replies") or 0):
        return None
    history: list[tuple[str, str]] = []
    for x in [first, *earlier]:
        history += [("user", x.message), ("assistant", x.reply_text or "")]
    return history


async def _over_cap(session: AsyncSession) -> bool:
    cap = int(await get_setting(session, "comment_max_replies_per_hour") or 0)
    if cap <= 0:
        return False
    n = (await session.execute(select(func.count()).select_from(PostComment).where(
        PostComment.replied_at >= datetime.now(timezone.utc) - timedelta(hours=1)))).scalar_one()
    return n >= cap


async def classify(session: AsyncSession, llm: LLMService, text: str, post_text: str | None,
                   history: list[tuple[str, str]] = ()) -> dict:
    """{"kind", "reason", "confidence", "language", "question", "reply"}; word rules when no AI route answers.
    Also the Post comments page's "Check a comment" tool."""
    if not text.strip() or _ONLY_EMOJI.match(text):
        return {"kind": "reaction", "reason": "only emoji or no text", "confidence": 1.0, "language": "bn",
                "question": "", "reply": ""}
    thread = "".join(f"\n{'COMMENTER' if r == 'user' else 'PAGE'}: {t[:300]}" for r, t in history)
    content = f"POST: {(post_text or '(not available)')[:1500]}" + (f"\nTHREAD SO FAR:{thread}" if thread else "") \
        + f"\nCOMMENT: {text[:1500]}"

    def build(policy):
        return PrivacyGuard(policy).prepare_task(CLASSIFY_SYSTEM, content)
    try:
        out = await llm.run(session, "router", build, channel=CHANNEL)
        data = parse_json_object(out.result.text) or {}
        restore = out.request.redactor.restore
    except LLMUnavailable:
        data, restore = {}, (lambda s: s)
    if data.get("kind") in KINDS:
        kind, reason = data["kind"], restore(str(data.get("reason") or "")).strip()[:300]
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
    else:  # no AI: only what words can tell for sure; nothing is removed on a guess
        kind = "spam" if _LINK.search(text) else "question" if "?" in text else "other"
        reason, confidence = "word rules only (no AI route answered)", 0.4
    lang = data.get("language") if data.get("language") in ("bn", "en") else detect(text)
    return {"kind": kind, "reason": reason, "confidence": confidence, "language": lang,
            "question": restore(str(data.get("question") or "")).strip(), "reply": restore(str(data.get("reply") or "")).strip()}


async def plan(session: AsyncSession, llm: LLMService, c: PostComment, history: list[tuple[str, str]],
               embedder=None, *, answer: bool = True) -> None:
    """Sets kind, reason, confidence, action (hide | delete | notify | None) and reply_text / reply_path: what we would
    do. Nothing is sent here. answer=False: only moderation (someone else's thread)."""
    from app.services.pipeline import run_pipeline   # local: pipeline imports a lot
    got = await classify(session, llm, c.message or "", c.post_text, history)
    c.kind, c.reason, c.confidence, lang = got["kind"], got["reason"] or None, got["confidence"], got["language"]
    c.reply_text = c.reply_path = c.action = None
    if c.kind in ("spam", "abuse"):
        act = await get_setting(session, "comment_spam_action") or "ignore"
        c.action = act if act in ("hide", "delete") else None
        return
    if c.kind == "against":
        act = await get_setting(session, "comment_against_action") or "notify"
        c.action = act if act in ("hide", "delete") else "notify"
        return
    if not answer:
        return
    if c.kind == "question":
        chars = int(await get_setting(session, "comment_max_chars") or 300)
        question = got["question"] or c.message
        res = await run_pipeline(session, llm, text=c.message, profile=None, conversation=None, channel=CHANNEL,
                                 history=history, persist=False, embedder=embedder, standalone_question=question,
                                 guest_request=question if question != c.message else None, guest_language=lang,
                                 extra_rules=COMMENT_RULES.format(post=(c.post_text or "(not available)")[:1500], chars=chars))
        if res.path in ANSWERED and res.text.strip():
            c.reply_text, c.reply_path = shorten(_AGENT_HINT.sub("", res.text).strip(), chars + 150) or None, res.path
        if not c.reply_text:  # the knowledge does not have it: a polite pointer to the inbox, never a guess
            c.reply_text, c.reply_path = await localized(session, "comment_no_answer", c.message, lang), "canned"
    elif c.kind in ("praise", "greeting") and await get_setting(session, "comment_reply_to_praise"):
        c.reply_text = got["reply"] or await localized(session, "comment_thanks_reply", c.message, lang)
        c.reply_path = "ai_short" if got["reply"] else "canned"
    elif c.kind == "complaint":
        c.reply_text, c.reply_path = await localized(session, "comment_complaint_reply", c.message, lang), "canned"


async def _notify(session: AsyncSession, c: PostComment) -> None:
    """Tell staff now (the alert webhook, if one is set). Best effort: the comment is on the page as 'Needs staff'."""
    from app.services.monitoring import Alert, send_alerts
    what = {"hide": "hidden", "delete": "deleted"}.get(c.action or "", "left on the page")
    text = (f"A comment against the organisation on {'Instagram' if ig(c) else 'the Facebook Page'} ({what}): \"{(c.message or '')[:200]}\""
            + (f" by {c.from_name}" if c.from_name else "") + (f". Why: {c.reason}" if c.reason else "")
            + (f". Open it: {c.post_url or 'Instagram'}" if ig(c) else f". Open it: https://www.facebook.com/{c.comment_id}"))
    try:
        await send_alerts(session, [Alert(f"comment:{c.comment_id}", text)])
    except Exception:  # noqa: BLE001 - never lose the comment's own processing for an alert
        logger.exception("comment alert failed for %s", c.comment_id)


async def publish(session: AsyncSession, c: PostComment, creds) -> None:
    """Carry out the plan. A failed like or private reply is noted, not fatal; a failed reply or removal leaves the
    comment 'failed' with the plan kept, for staff to try again."""
    notes: list[str] = []
    if c.action in ("hide", "delete"):
        try:
            await (pc.hide if c.action == "hide" else pc.delete)(c.comment_id, creds, ig=ig(c))
        except GraphError as exc:
            c.status, c.error = "failed", f"{c.action} failed: {exc.message}"[:500]
            return
        c.status = "hidden" if c.action == "hide" else "deleted"
        if c.kind == "against":
            await _notify(session, c)
        return
    if c.kind == "against":   # notify only: a person decides; nothing public from the AI
        c.status = "needs_staff"
        await _notify(session, c)
        return
    if c.kind in ("spam", "abuse"):
        c.status = "ignored"
        return
    if not c.liked and c.source != "scan" and not ig(c) and await get_setting(session, "comment_auto_like") and c.kind != "complaint":
        try:
            await pc.like(c.comment_id, creds)
            c.liked = True
        except GraphError as exc:
            notes.append(f"like failed: {exc.message}")
    if c.reply_text and not c.reply_comment_id:
        try:
            c.reply_comment_id = await pc.reply(c.comment_id, c.reply_text, creds, ig=ig(c)) or "posted"
            c.replied_at = datetime.now(timezone.utc)
        except GraphError as exc:
            c.status, c.error = "failed", f"reply failed: {exc.message}"[:500]
            return
    if c.kind == "question" and not c.private_replied and not ig(c) and await get_setting(session, "comment_private_reply"):
        try:
            await pc.private_reply(c.comment_id, await localized(session, "comment_private_reply_text", c.message), creds)
            c.private_replied = True
        except GraphError as exc:
            notes.append(f"private reply failed: {exc.message}")
    c.error = "; ".join(notes)[:500] or None
    c.status = ("needs_staff" if c.kind == "complaint" else "replied" if c.reply_comment_id
                else "liked" if c.liked else "ignored")
