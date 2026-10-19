"""Greeting matching rules. The greeting words themselves - every common way a guest opens a chat (Islamic,
Hindu/neutral, time of day, formal and casual English, Bangla script, Banglish, misspellings) - live in
app/defaults/messages_and_handover.json under "greetings" -> "greeting_words", grouped by kind; festival wishes and
"how are you" are small talk there.

Matching (services/greeting.py) ignores case, punctuation, emoji and stretched letters ("hiii", "salaaam"), and a
message may combine greetings and courtesy words ("hi, assalamu alaikum sir", "salam apu, good morning").
"""
from app.services.message_defaults import MESSAGE_DEFAULTS

GREETING_WORDS: list[str] = MESSAGE_DEFAULTS["greeting_words"]

# Greetings answered in kind: the formal reply opens with the guest's own greeting instead of the Islamic salam.
REPLY_OPENINGS = [
    (("নমস্কার", "নমস্তে", "প্রণাম", "nomoskar", "nomoshkar", "namaskar", "nomosker", "nomoskaar", "namaste", "namastey",
      "pronam", "pranam", "জয় গুরু", "joy guru", "রাধে রাধে", "radhe radhe"), "নমস্কার", "Namaskar"),
    (("আদাব", "আদাব আরজ", "adab", "adaab", "adab arz"), "আদাব", "Adab"),
]

# Emoji a guest may send on its own to say hello (not 🙂/🙏: mid-chat those mean "ok" / "thanks")
GREETING_EMOJI = {"👋", "🙋", "🙋‍♂️", "🙋‍♀️", "🤝"}
