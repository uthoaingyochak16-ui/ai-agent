from app.channels.base import ChannelAdapter
from app.channels.meta_messaging import InstagramAdapter, MessengerAdapter
from app.channels.whatsapp import WhatsAppAdapter

_ADAPTERS: dict[str, ChannelAdapter] = {a.channel: a for a in (MessengerAdapter(), InstagramAdapter(), WhatsAppAdapter())}
CHANNELS = tuple(_ADAPTERS)


def get_adapter(channel: str) -> ChannelAdapter:
    try:
        return _ADAPTERS[channel]
    except KeyError:
        raise KeyError(f"unknown channel: {channel}") from None
