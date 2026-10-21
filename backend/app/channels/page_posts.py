"""Posts on Facebook Pages and Instagram accounts through the Graph API: publish, and read past posts with their numbers.

Permissions: Facebook Page token: pages_manage_posts (publish), pages_read_engagement + read_insights (numbers).
Instagram: instagram_content_publish (publish, image posts only: Instagram has no text-only post),
instagram_manage_insights (numbers). Meta retires insight metrics from time to time; a metric it refuses is left out
(the counts of reactions / comments / shares still come with the post itself).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.channels.base import ChannelCredentials
from app.channels.graph import GraphError, graph_request

logger = logging.getLogger(__name__)
FB_REACH = ("post_impressions_unique", "post_impressions", "post_clicks")
IG_INSIGHTS = ("reach", "saved", "shares", "views")


@dataclass
class RemotePost:
    external_id: str
    message: str
    created: datetime | None
    permalink: str | None
    media_type: str | None
    image_url: str | None = None
    metrics: dict = field(default_factory=dict)


async def _call(method: str, path: str, creds: ChannelCredentials, **kw) -> dict:
    return await graph_request(method, path, token=creds.access_token, base_url=creds.base_url, version=creds.version, **kw)


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


# ---- publish -----------------------------------------------------------------------------------------------------
async def publish_facebook(page_id: str, creds: ChannelCredentials, *, message: str, link: str | None = None,
                           image_url: str | None = None) -> tuple[str, str | None]:
    """-> (post id, permalink). A photo post when there is an image, else a text / link post."""
    if image_url:
        d = await _call("POST", f"{page_id}/photos", creds, json={"url": image_url, "caption": message, "published": True})
        pid = d.get("post_id") or d.get("id")
    else:
        body = {"message": message, **({"link": link} if link else {})}
        pid = (await _call("POST", f"{page_id}/feed", creds, json=body)).get("id")
    if not pid:
        raise GraphError(500, None, None, "Facebook did not return the new post's id")
    try:
        link_ = (await _call("GET", pid, creds, params={"fields": "permalink_url"})).get("permalink_url")
    except GraphError:
        link_ = None
    return str(pid), link_


async def publish_instagram(account_id: str, creds: ChannelCredentials, *, caption: str, image_url: str) -> tuple[str, str | None]:
    """Instagram publishes in two steps: a media container, then publishing it."""
    container = (await _call("POST", f"{account_id}/media", creds, json={"image_url": image_url, "caption": caption})).get("id")
    if not container:
        raise GraphError(500, None, None, "Instagram did not accept the image")
    mid = (await _call("POST", f"{account_id}/media_publish", creds, json={"creation_id": container})).get("id")
    if not mid:
        raise GraphError(500, None, None, "Instagram did not publish the post")
    try:
        link_ = (await _call("GET", mid, creds, params={"fields": "permalink"})).get("permalink")
    except GraphError:
        link_ = None
    return str(mid), link_


# ---- read posts and their numbers ----------------------------------------------------------------------------------
async def _insights(path: str, creds: ChannelCredentials, metrics: tuple[str, ...]) -> dict:
    """The metrics Meta still gives; one refused metric must not lose the others."""
    out: dict = {}
    try:
        d = await _call("GET", f"{path}/insights", creds, params={"metric": ",".join(metrics)})
        for m in d.get("data", []):
            vals = m.get("values") or [{}]
            v = vals[0].get("value") if vals else m.get("value")
            if isinstance(v, (int, float)):
                out[m.get("name")] = v
        return out
    except GraphError:
        pass
    for name in metrics:   # one by one: keep those that work
        try:
            d = await _call("GET", f"{path}/insights", creds, params={"metric": name})
            v = ((d.get("data") or [{}])[0].get("values") or [{}])[0].get("value")
            if isinstance(v, (int, float)):
                out[name] = v
        except GraphError:
            continue
    return out


async def facebook_posts(page_id: str, creds: ChannelCredentials, *, limit: int = 50, insights: bool = True) -> list[RemotePost]:
    d = await _call("GET", f"{page_id}/posts", creds, params={
        "fields": "id,message,created_time,permalink_url,status_type,full_picture,attachments{media_type},"
                  "shares,reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)",
        "limit": max(1, min(limit, 100))})
    out: list[RemotePost] = []
    for p in d.get("data", []):
        att = ((p.get("attachments") or {}).get("data") or [{}])[0]
        kind = (att.get("media_type") or p.get("status_type") or "text").lower()
        m = {"reactions": ((p.get("reactions") or {}).get("summary") or {}).get("total_count", 0),
             "comments": ((p.get("comments") or {}).get("summary") or {}).get("total_count", 0),
             "shares": (p.get("shares") or {}).get("count", 0)}
        if insights:
            got = await _insights(p["id"], creds, FB_REACH)
            m.update({k: v for k, v in (("reach", got.get("post_impressions_unique")), ("impressions", got.get("post_impressions")),
                                        ("clicks", got.get("post_clicks"))) if v is not None})
        out.append(RemotePost(str(p["id"]), p.get("message") or "", _when(p.get("created_time")), p.get("permalink_url"),
                              "photo" if kind in ("photo", "added_photos") else "video" if "video" in kind else
                              "link" if kind in ("share", "link", "shared_story") else kind[:24], p.get("full_picture"), m))
    return out


async def instagram_posts(account_id: str, creds: ChannelCredentials, *, limit: int = 50, insights: bool = True) -> list[RemotePost]:
    d = await _call("GET", f"{account_id}/media", creds, params={
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,media_url,thumbnail_url",
        "limit": max(1, min(limit, 100))})
    out: list[RemotePost] = []
    for p in d.get("data", []):
        m = {"reactions": p.get("like_count", 0), "comments": p.get("comments_count", 0)}
        if insights:
            got = await _insights(p["id"], creds, IG_INSIGHTS)
            m.update({k: got[n] for k, n in (("reach", "reach"), ("saves", "saved"), ("shares", "shares"), ("views", "views")) if n in got})
        kind = "reel" if p.get("media_product_type") == "REELS" else {"IMAGE": "photo", "VIDEO": "video", "CAROUSEL_ALBUM": "carousel"}.get(p.get("media_type", ""), "photo")
        out.append(RemotePost(str(p["id"]), p.get("caption") or "", _when(p.get("timestamp")), p.get("permalink"), kind,
                              p.get("media_url") or p.get("thumbnail_url"), m))
    return out
