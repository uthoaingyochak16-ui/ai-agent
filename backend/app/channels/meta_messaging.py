"""Messenger and Instagram DMs share the same payload shape (entry[].messaging[])."""
from __future__ import annotations

from app.channels.base import (ChannelAdapter, ChannelCredentials, IncomingMessage, OutOfWindowError, SendResult,
                               TemplateMessage, UserProfile, ms_to_dt, split_text)
from app.channels.graph import GraphError, graph_request

# (code, subcode) pairs Meta returns when the 24h/7d window is closed. Verify against Meta's error reference.
_WINDOW_ERRORS = {(10, 2018278)}


class _MetaMessagingAdapter(ChannelAdapter):
    def parse_incoming(self, payload: dict) -> list[IncomingMessage]:
        if payload.get("object") != self.webhook_object:
            return []
        out: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for ev in entry.get("messaging", []):
                sender = (ev.get("sender") or {}).get("id")
                recipient = (ev.get("recipient") or {}).get("id") or entry.get("id")
                msg = ev.get("message")
                if not sender or not msg or msg.get("is_echo"):
                    continue  # delivery/read receipts, echoes of our own sends
                mid = msg.get("mid")
                if not mid:
                    continue
                text = msg.get("text") or (msg.get("quick_reply") or {}).get("payload") or ""
                atts = [{"type": a.get("type"), "url": (a.get("payload") or {}).get("url")}
                        for a in msg.get("attachments", [])]
                if not text and not atts:
                    continue
                out.append(IncomingMessage(channel=self.channel, external_user_id=str(sender), text=text,
                                           attachments=atts, timestamp=ms_to_dt(ev.get("timestamp", 0)),
                                           message_id=mid, recipient_id=str(recipient) if recipient else None))
            for ev in entry.get("messaging", []):  # postbacks (buttons) -> treated as text
                pb = ev.get("postback")
                sender = (ev.get("sender") or {}).get("id")
                if pb and sender and pb.get("mid"):
                    out.append(IncomingMessage(channel=self.channel, external_user_id=str(sender),
                                               text=pb.get("title") or pb.get("payload") or "", attachments=[],
                                               timestamp=ms_to_dt(ev.get("timestamp", 0)), message_id=pb["mid"],
                                               recipient_id=(ev.get("recipient") or {}).get("id")))
        return out

    async def send_message(self, to, text, creds: ChannelCredentials, *, template: TemplateMessage | None = None,
                           human_agent_tag: bool = False) -> SendResult:
        last: dict = {}
        for chunk in split_text(text, self.max_text):
            body: dict = {"recipient": {"id": to}, "message": {"text": chunk}}
            if human_agent_tag:
                body.update({"messaging_type": "MESSAGE_TAG", "tag": "HUMAN_AGENT"})
            else:
                body["messaging_type"] = "RESPONSE"
            try:
                last = await graph_request("POST", "me/messages", token=creds.access_token, base_url=creds.base_url,
                                           version=creds.version, json=body)
            except GraphError as e:
                if (e.code, e.subcode) in _WINDOW_ERRORS:
                    raise OutOfWindowError(self.channel) from e
                raise
        return SendResult(message_id=last.get("message_id"), raw=last)

    async def mark_read(self, to, message_id, creds: ChannelCredentials) -> None:
        await graph_request("POST", "me/messages", token=creds.access_token, base_url=creds.base_url,
                            version=creds.version, json={"recipient": {"id": to}, "sender_action": "mark_seen"})

    async def show_typing(self, to, message_id, creds: ChannelCredentials) -> None:
        await graph_request("POST", "me/messages", token=creds.access_token, base_url=creds.base_url,
                            version=creds.version, json={"recipient": {"id": to}, "sender_action": "typing_on"})

    _profile_fields = "name,profile_pic"

    async def other_page_ids(self, user_id, creds: ChannelCredentials) -> list[str]:
        # Works only when the Pages and the app belong to the same Business Manager; otherwise Meta returns an error.
        d = await graph_request("GET", f"{user_id}/ids_for_pages", token=creds.access_token, base_url=creds.base_url,
                                version=creds.version)
        return [str(x["id"]) for x in d.get("data", []) if x.get("id") and str(x["id"]) != str(user_id)]

    async def fetch_profile(self, user_id, creds: ChannelCredentials) -> UserProfile | None:
        d = await graph_request("GET", user_id, token=creds.access_token, base_url=creds.base_url,
                                version=creds.version, params={"fields": self._profile_fields})
        name = d.get("name") or " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x) or d.get("username")
        return UserProfile(name=name or None, avatar_url=d.get("profile_pic"))


class MessengerAdapter(_MetaMessagingAdapter):
    channel = "messenger"
    webhook_object = "page"
    max_text = 2000
    _profile_fields = "first_name,last_name,profile_pic"


class InstagramAdapter(_MetaMessagingAdapter):
    channel = "instagram"
    webhook_object = "instagram"
    max_text = 1000
    _profile_fields = "name,username,profile_pic"
