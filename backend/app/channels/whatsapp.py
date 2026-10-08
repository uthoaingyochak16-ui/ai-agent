from __future__ import annotations

from datetime import datetime, timezone

from app.channels.base import (ChannelAdapter, ChannelCredentials, IncomingMessage, OutOfWindowError, SendResult,
                               TemplateMessage, split_text)
from app.channels.graph import GraphError, graph_request

_REENGAGEMENT_CODE = 131047  # "Re-engagement message": >24h since the user's last message. Verify vs. Meta docs.


class WhatsAppAdapter(ChannelAdapter):
    channel = "whatsapp"
    webhook_object = "whatsapp_business_account"
    max_text = 4000

    def parse_incoming(self, payload: dict) -> list[IncomingMessage]:
        if payload.get("object") != self.webhook_object:
            return []
        out: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                for m in value.get("messages", []):  # "statuses" (delivery receipts) are ignored on purpose
                    sender, mid = m.get("from"), m.get("id")
                    if not sender or not mid:
                        continue
                    text, atts, mtype = "", [], m.get("type")
                    if mtype == "text":
                        text = (m.get("text") or {}).get("body", "")
                    elif mtype == "button":
                        text = (m.get("button") or {}).get("text", "")
                    elif mtype == "interactive":
                        it = m.get("interactive") or {}
                        reply = it.get("button_reply") or it.get("list_reply") or {}
                        text = reply.get("title", "")
                    elif mtype in ("image", "audio", "video", "document", "sticker"):
                        media = m.get(mtype) or {}
                        text = media.get("caption", "")
                        atts.append({"type": mtype, "id": media.get("id"), "mime": media.get("mime_type")})
                    elif mtype == "location":
                        loc = m.get("location") or {}
                        atts.append({"type": "location", "lat": loc.get("latitude"), "lng": loc.get("longitude")})
                    else:
                        continue
                    if not text and not atts:
                        continue
                    ts = datetime.fromtimestamp(int(m.get("timestamp", 0)), tz=timezone.utc)
                    out.append(IncomingMessage(channel="whatsapp", external_user_id=str(sender), text=text,
                                               attachments=atts, timestamp=ts, message_id=mid,
                                               recipient_id=phone_number_id, sender_name=names.get(sender)))
        return out

    async def send_message(self, to, text, creds: ChannelCredentials, *, template: TemplateMessage | None = None,
                           human_agent_tag: bool = False) -> SendResult:
        path = f"{creds.account_id}/messages"
        if template is not None:
            body = {"messaging_product": "whatsapp", "to": to, "type": "template",
                    "template": {"name": template.name, "language": {"code": template.language}}}
            if template.body_params:
                body["template"]["components"] = [{"type": "body", "parameters": [
                    {"type": "text", "text": p} for p in template.body_params]}]
            res = await graph_request("POST", path, token=creds.access_token, base_url=creds.base_url,
                                      version=creds.version, json=body)
            return SendResult(message_id=((res.get("messages") or [{}])[0]).get("id"), raw=res)
        last: dict = {}
        for chunk in split_text(text, self.max_text):
            body = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": chunk, "preview_url": False}}
            try:
                last = await graph_request("POST", path, token=creds.access_token, base_url=creds.base_url,
                                           version=creds.version, json=body)
            except GraphError as e:
                if e.code == _REENGAGEMENT_CODE:
                    raise OutOfWindowError("whatsapp", needs_template=True) from e
                raise
        return SendResult(message_id=((last.get("messages") or [{}])[0]).get("id"), raw=last)

    async def mark_read(self, to, message_id, creds: ChannelCredentials) -> None:
        await graph_request("POST", f"{creds.account_id}/messages", token=creds.access_token,
                            base_url=creds.base_url, version=creds.version,
                            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})

    async def show_typing(self, to, message_id, creds: ChannelCredentials) -> None:
        if not message_id:  # WhatsApp ties the indicator to the customer's message (it also marks it read)
            return
        await graph_request("POST", f"{creds.account_id}/messages", token=creds.access_token,
                            base_url=creds.base_url, version=creds.version,
                            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id,
                                  "typing_indicator": {"type": "text"}})
