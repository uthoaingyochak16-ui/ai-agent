from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class IncomingMessage:
    """The one internal format every channel is normalized to."""
    channel: str
    external_user_id: str
    text: str
    attachments: list[dict]
    timestamp: datetime
    message_id: str
    recipient_id: str | None = None   # page id / ig id / phone_number_id the message was sent to
    sender_name: str | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "IncomingMessage":
        d = dict(d)
        d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)


@dataclass
class ChannelCredentials:
    access_token: str
    account_id: str | None = None
    base_url: str = "https://graph.facebook.com"
    version: str = "v21.0"


@dataclass
class TemplateMessage:
    name: str
    language: str = "en"
    body_params: list[str] = field(default_factory=list)


@dataclass
class SendResult:
    message_id: str | None
    raw: dict


class OutOfWindowError(Exception):
    """The messaging window is closed (or Meta says so)."""
    def __init__(self, channel: str, needs_template: bool = False):
        super().__init__(f"{channel}: outside the allowed messaging window")
        self.channel = channel
        self.needs_template = needs_template


def ms_to_dt(ms: int | float) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def split_text(text: str, limit: int) -> list[str]:
    """Split on paragraph/sentence/space boundaries so each chunk fits the channel limit."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(". ", 0, limit) + 1, text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


class ChannelAdapter(ABC):
    channel: str
    webhook_object: str
    max_text: int = 1000

    def verify_webhook(self, params: Mapping[str, str], verify_token: str) -> str | None:
        """GET handshake. Returns the challenge to echo, or None if verification fails."""
        import hmac
        if params.get("hub.mode") != "subscribe":
            return None
        given = (params.get("hub.verify_token") or "").strip()  # pasted tokens often carry a stray space
        if not verify_token or not hmac.compare_digest(given.encode(), verify_token.strip().encode()):
            return None
        return params.get("hub.challenge")

    @abstractmethod
    def parse_incoming(self, payload: dict) -> list[IncomingMessage]: ...

    @abstractmethod
    async def send_message(self, to: str, text: str, creds: ChannelCredentials, *,
                           template: TemplateMessage | None = None,
                           human_agent_tag: bool = False) -> SendResult: ...

    @abstractmethod
    async def mark_read(self, to: str, message_id: str, creds: ChannelCredentials) -> None: ...

    async def show_typing(self, to: str, message_id: str | None, creds: ChannelCredentials) -> None:
        """Typing bubble on the customer's side. Meta clears it after ~20-25 s or when a message is sent."""

    async def other_page_ids(self, user_id: str, creds: ChannelCredentials) -> list[str]:
        """The same person's ids on other Pages of the same Business (Meta-verified identity link)."""
        return []

    async def fetch_profile(self, user_id: str, creds: ChannelCredentials) -> UserProfile | None:
        """Public name / profile photo of a customer, where the channel exposes it."""
        return None


@dataclass
class UserProfile:
    name: str | None
    avatar_url: str | None
