"""Post comments of Facebook Pages (the `feed` webhook field) and Instagram accounts (the `comments` field): in, and
replies / likes / hides / deletes out through the Graph API. `ig=True` selects Instagram's endpoints (replies under
/replies, hide with ?hide=, no likes: the API cannot like an Instagram comment).

Needs the Page token permissions pages_read_engagement + pages_read_user_content (read comments and who wrote them),
pages_manage_engagement (reply, like, hide, delete) and pages_manage_metadata (subscribe the Page to `feed`); a private reply
(one Messenger message answering a comment) also needs pages_messaging. Pages can only *like* a comment through the
API: other reactions (love, care ...) are not available to apps.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.channels.base import ChannelCredentials
from app.channels.graph import graph_request


@dataclass
class CommentEvent:
    page_id: str
    post_id: str
    comment_id: str
    parent_id: str | None
    from_id: str | None
    from_name: str | None
    message: str
    post_text: str | None = None   # known when we read the post ourselves (scan); the webhook does not carry it
    channel: str = "facebook"      # facebook | instagram
    post_url: str | None = None


def parse_feed(payload: dict) -> list[CommentEvent]:
    """New comments in a `page` webhook (entry[].changes[] with field "feed"). Edits, deletions, reactions, new posts
    and the Page's own comments (our replies come back through the same webhook) are left out."""
    if payload.get("object") != "page":
        return []
    out: list[CommentEvent] = []
    for entry in payload.get("entry", []):
        page_id = str(entry.get("id") or "")
        for ch in entry.get("changes", []):
            v = ch.get("value") or {}
            if ch.get("field") != "feed" or v.get("item") != "comment" or v.get("verb") != "add":
                continue
            who = v.get("from") or {}
            if not v.get("comment_id") or not v.get("post_id") or (who.get("id") and str(who["id"]) == page_id):
                continue
            out.append(CommentEvent(page_id=page_id, post_id=str(v["post_id"]), comment_id=str(v["comment_id"]),
                                    parent_id=str(v["parent_id"]) if v.get("parent_id") else None,
                                    from_id=str(who["id"]) if who.get("id") else None, from_name=who.get("name"),
                                    message=(v.get("message") or "").strip()))
    return out


def parse_ig_comments(payload: dict) -> list[CommentEvent]:
    """New comments in an `instagram` webhook (entry[].changes[] with field "comments"); the account's own are left out."""
    if payload.get("object") != "instagram":
        return []
    out: list[CommentEvent] = []
    for entry in payload.get("entry", []):
        acc = str(entry.get("id") or "")
        for ch in entry.get("changes", []):
            v = ch.get("value") or {}
            who = v.get("from") or {}
            media = v.get("media") or {}
            if ch.get("field") != "comments" or not v.get("id") or not media.get("id") or str(who.get("id") or "") == acc:
                continue
            out.append(CommentEvent(page_id=acc, post_id=str(media["id"]), comment_id=str(v["id"]),
                                    parent_id=str(v.get("parent_id") or media["id"]), from_id=str(who["id"]) if who.get("id") else None,
                                    from_name=who.get("username"), message=(v.get("text") or "").strip(), channel="instagram"))
    return out


async def _call(method: str, path: str, creds: ChannelCredentials, **kw) -> dict:
    return await graph_request(method, path, token=creds.access_token, base_url=creds.base_url, version=creds.version, **kw)


async def reply(comment_id: str, text: str, creds: ChannelCredentials, *, ig: bool = False) -> str | None:
    """A public reply under the comment. Returns the new comment's id."""
    return (await _call("POST", f"{comment_id}/{'replies' if ig else 'comments'}", creds, json={"message": text})).get("id")


async def like(comment_id: str, creds: ChannelCredentials) -> None:
    await _call("POST", f"{comment_id}/likes", creds)


async def hide(comment_id: str, creds: ChannelCredentials, *, ig: bool = False) -> None:
    """Hidden comments stay visible to their author and friends, so it is quieter than deleting."""
    if ig:
        await _call("POST", comment_id, creds, params={"hide": "true"})
    else:
        await _call("POST", comment_id, creds, json={"is_hidden": True})


async def unhide(comment_id: str, creds: ChannelCredentials, *, ig: bool = False) -> None:
    if ig:
        await _call("POST", comment_id, creds, params={"hide": "false"})
    else:
        await _call("POST", comment_id, creds, json={"is_hidden": False})


async def delete(comment_id: str, creds: ChannelCredentials, *, ig: bool = False) -> None:  # noqa: ARG001 - same call
    """Gone for good (the Page may delete comments on its own posts). Prefer hide, which can be undone."""
    await _call("DELETE", comment_id, creds)


async def recent_comments(page_id: str, creds: ChannelCredentials, *, posts: int = 10, per_post: int = 100) -> list[CommentEvent]:
    """Comments (and replies, filter=stream) on the Page's latest posts, the Page's own left out."""
    feed = await _call("GET", f"{page_id}/posts", creds, params={"fields": "id,message,story", "limit": max(1, min(posts, 50))})
    out: list[CommentEvent] = []
    for post in feed.get("data", []):
        d = await _call("GET", f"{post['id']}/comments", creds, params={
            "fields": "id,message,from,parent{id}", "filter": "stream", "limit": max(1, min(per_post, 200))})
        for x in d.get("data", []):
            who = x.get("from") or {}
            if who.get("id") and str(who["id"]) == str(page_id):
                continue
            out.append(CommentEvent(page_id=str(page_id), post_id=str(post["id"]), comment_id=str(x["id"]),
                                    parent_id=str((x.get("parent") or {}).get("id") or post["id"]),
                                    from_id=str(who["id"]) if who.get("id") else None, from_name=who.get("name"),
                                    message=(x.get("message") or "").strip(),
                                    post_text=(post.get("message") or post.get("story") or "").strip()[:3000]))
    return out


async def private_reply(comment_id: str, text: str, creds: ChannelCredentials) -> None:
    """One Messenger message to the commenter (allowed once per comment, within 7 days). Their answer arrives as a normal
    Messenger message, so the chat assistant takes it from there."""
    await _call("POST", "me/messages", creds, json={"recipient": {"comment_id": comment_id}, "message": {"text": text}})


async def ig_recent_comments(account_id: str, creds: ChannelCredentials, *, posts: int = 10, per_post: int = 100) -> list[CommentEvent]:
    """Comments (and replies) on the Instagram account's latest posts, its own left out."""
    media = await _call("GET", f"{account_id}/media", creds, params={"fields": "id,caption,permalink", "limit": max(1, min(posts, 50))})
    out: list[CommentEvent] = []
    for m in media.get("data", []):
        d = await _call("GET", f"{m['id']}/comments", creds, params={
            "fields": "id,text,username,from,replies{id,text,username,from}", "limit": max(1, min(per_post, 200))})
        for x in d.get("data", []):
            for c, parent in [(x, m["id"]), *((r, x["id"]) for r in (x.get("replies") or {}).get("data", []))]:
                who = c.get("from") or {}
                if str(who.get("id") or "") == str(account_id):
                    continue
                out.append(CommentEvent(page_id=str(account_id), post_id=str(m["id"]), comment_id=str(c["id"]), parent_id=str(parent),
                                        from_id=str(who["id"]) if who.get("id") else None, from_name=c.get("username") or who.get("username"),
                                        message=(c.get("text") or "").strip(), post_text=(m.get("caption") or "").strip()[:3000],
                                        channel="instagram", post_url=m.get("permalink")))
    return out


async def ig_post(media_id: str, creds: ChannelCredentials) -> tuple[str, str | None]:
    d = await _call("GET", media_id, creds, params={"fields": "caption,permalink"})
    return (d.get("caption") or "").strip(), d.get("permalink")


async def post_text(post_id: str, creds: ChannelCredentials) -> str:
    d = await _call("GET", post_id, creds, params={"fields": "message,story"})
    return (d.get("message") or d.get("story") or "").strip()


async def parent_from(parent_id: str, creds: ChannelCredentials) -> str | None:
    """Who wrote the comment this one answers (to tell a reply to our reply from a reply to someone else)."""
    d = await _call("GET", parent_id, creds, params={"fields": "from"})
    return str((d.get("from") or {}).get("id") or "") or None
