from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

DEFAULTS: dict[str, object] = {
    "handover_keywords": ["agent", "human", "live agent", "real person", "representative", "customer care",
                          "এজেন্ট", "মানুষ", "কাস্টমার কেয়ার", "হিউম্যান"],
    # Canned replies: the plain key is Bangla, `<key>_en` is sent instead when the guest writes in English
    # (Banglish counts as Bangla). One language per reply, never both at once.
    "handover_ack": "আমি আপনাকে আমাদের একজন প্রতিনিধির সাথে যুক্ত করছি, শীঘ্রই এখানেই উত্তর পাবেন।",
    "handover_ack_en": "I'm connecting you with a team member. They will reply here shortly.",
    "erasure_ack": "আপনার ব্যক্তিগত তথ্য মুছে ফেলা হয়েছে।",
    "erasure_ack_en": "Done. Your personal data has been deleted.",
    "merge_policy": "suggest",            # typed contacts are unverified; verified WhatsApp numbers still link automatically
    "human_agent_tag_enabled": False,
    "ai_auto_resume_minutes": 30,         # a chat paused by staff goes back to the AI after this much staff silence; 0 = never     # Messenger/IG HUMAN_AGENT tag (7-day window for human replies)
    "name_mode": "placeholder",           # placeholder | first_name | none
    "stub_reply": "Thanks for your message! A team member will get back to you soon.",
    "extraction_every_n_messages": 4,
    "summarize_every_n_messages": 8,
    # --- LLM / budget
    "monthly_budget_usd": 0,              # 0 = unlimited
    "budget_alert_percent": 80,
    "budget_fallback_enabled": True,      # switch to the cheap route once the budget is exhausted
    "budget_fallback_route": None,        # {"provider": "...", "model": "..."}
    "budget_hard_stop": False,            # stop calling the LLM entirely when exhausted
    "llm_retries": 1,
    # --- privacy
    "privacy_custom_patterns": [],
    "privacy_never_send": [],
    "system_prompt": None,
    # --- retrieval / pipeline
    "top_k": 3,
    "chunk_words": 120,                   # knowledge-base chunk size (words); re-process documents after changing
    "chunk_overlap_words": 20,
    "max_answer_chars": 450,              # replies are kept short (AI instructed; file/Q&A text trimmed); 0 = no limit
    "qa_text_threshold": 0.86,            # Q&A shortcut by plain wording similarity (typos, spelling variants)
    "history_len": 4,
    "chunk_token_budget": 800,
    "qa_threshold": 0.92,
    "cache_threshold": 0.95,
    "cache_ttl_days": 14,
    "retrieval_min_similarity": 0.25,
    "escalate_below": 0.45,
    "no_answer_reply": "দুঃখিত, এই বিষয়ে আমার কাছে এখন সঠিক তথ্য নেই। আপনি চাইলে আমাদের একজন প্রতিনিধির সাথে যুক্ত করে দিতে পারি — শুধু \"agent\" লিখুন।",
    "no_answer_reply_en": "Sorry, I don't have accurate information on that right now. I can connect you with a team member — just type \"agent\".",
    "consent_prompt": "আপনাকে আরও ভালোভাবে সাহায্য করতে আপনার নাম ও যোগাযোগের তথ্য সংরক্ষণ করতে পারি? হ্যাঁ/না লিখুন (যেকোনো সময় \"আমার তথ্য মুছে ফেলুন\" লিখতে পারেন)।",
    "consent_prompt_en": "By the way, may I save your name and contact details to serve you better? Reply yes or no (you can say 'delete my data' any time).",
    # --- greetings (bare "hi"/"hello"/"salam": canned reply, no AI call)
    "greeting_enabled": True,
    "greeting_first_reply": "আসসালামু আলাইকুম! আমি Quantum Method AI Assistant। আমি কীভাবে আপনাকে সাহায্য করতে পারি?",
    "greeting_return_reply": "আসসালামু আলাইকুম! আমি কীভাবে আপনাকে সাহায্য করতে পারি?",
    "greeting_repeat_reply": "জি, বলুন — আমি কীভাবে সাহায্য করতে পারি?",  # another "hi" inside the gap
    "greeting_gap_hours": 5,
    "greeting_words": ["hi", "hello", "hey", "hye", "hay", "hlw", "helo", "hellow", "hola", "yo",
                       "salam", "salaam", "slm", "aslm", "assalamualaikum", "assalamu alaikum", "asalamualaikum",
                       "as salamu alaikum", "assalamu alaykum", "assalam", "good morning", "good afternoon",
                       "good evening", "হাই", "হ্যালো", "হেলো", "সালাম", "আসসালামু আলাইকুম", "আসসালামুয়ালাইকুম",
                       "আস্সালামু আলাইকুম", "শুভ সকাল", "শুভ সন্ধ্যা"],
    # --- small talk: ready answers, no AI call (edit in AI Settings -> Messages)
    "smalltalk_enabled": True,
    "smalltalk": [
        {"questions": ["who are you", "who r u", "who are u", "what are you", "introduce yourself", "tumi ke", "apni ke",
                       "apni k", "আপনি কে", "তুমি কে", "আপনার পরিচয়", "apnar porichoy"],
         "answer": "আমি Quantum Method-এর AI Assistant। আপনার প্রশ্নের উত্তর দিতে ও প্রয়োজনীয় তথ্য জানাতে আমি এখানে আছি। "
                   "প্রয়োজনে আমাদের টিমের একজন সদস্যের সাথেও যুক্ত করে দিতে পারি।",
         "answer_en": "I'm the Quantum Method AI Assistant. I'm here to answer your questions and share the information you need, and I can connect you with a team member if needed."},
        {"questions": ["are you a bot", "are you a robot", "are you human", "are you real", "is this a bot", "is this ai",
                       "apni ki robot", "apni ki manush", "tumi ki robot", "আপনি কি রোবট", "আপনি কি মানুষ", "এটা কি বট"],
         "answer": "আমি একজন AI Assistant, মানুষ নই। তবে আপনি চাইলে আমাদের টিমের একজন সদস্যের সাথে কথা বলতে পারেন — শুধু লিখুন \"agent\"।",
         "answer_en": "I'm an AI assistant, not a person. If you'd like to talk to a team member, just type \"agent\"."},
        {"questions": ["what is your name", "whats your name", "your name", "apnar nam ki", "tomar nam ki", "apnar naam ki",
                       "আপনার নাম কি", "আপনার নাম কী", "তোমার নাম কি"],
         "answer": "আমার নাম Quantum Method AI Assistant। বলুন, আপনাকে কীভাবে সাহায্য করতে পারি?",
         "answer_en": "My name is Quantum Method AI Assistant. How can I help you?"},
        {"questions": ["how are you", "how r u", "how are u", "how are you doing", "hows it going", "how is everything",
                       "kemon achen", "kemon acho", "kmn achen", "ki obostha", "valo achen", "bhalo achen",
                       "কেমন আছেন", "কেমন আছো", "কি অবস্থা", "ভালো আছেন"],
         "answer": "ধন্যবাদ, আমি ভালো আছি! আশা করি আপনিও ভালো আছেন। বলুন, কীভাবে সাহায্য করতে পারি?",
         "answer_en": "I'm doing well, thank you! I hope you are too. How can I help you?"},
        {"questions": ["what do you do", "what does quantum method do", "what is quantum method", "what is this page",
                       "what is this page about", "tell me about you", "tell me about quantum method",
                       "apnara ki koren", "apnara ki niye kaj koren", "apnara ki niya kaj koren", "apnader kaj ki",
                       "quantum method ki", "আপনারা কি নিয়ে কাজ করেন", "আপনারা কী নিয়ে কাজ করেন", "আপনারা কি করেন",
                       "আপনাদের কাজ কি", "কোয়ান্টাম মেথড কি", "কোয়ান্টাম মেথড কী"],
         "answer": "Quantum Method মেডিটেশন ও আত্মউন্নয়নমূলক কোর্স এবং কার্যক্রম নিয়ে কাজ করে। "
                   "কোর্স, ফি, সময়সূচি বা ভর্তি সম্পর্কে জানতে চাইলে প্রশ্ন করুন — আমি জানিয়ে দেব।",
         "answer_en": "Quantum Method offers meditation and self-development courses and programmes. Ask me about courses, fees, schedules or admission."},
        {"questions": ["what can you do", "how can you help", "how can you help me", "what can i ask", "ki ki jante parbo",
                       "apni ki ki korte paren", "আপনি কী কী করতে পারেন", "কি কি জানতে পারবো", "কীভাবে সাহায্য করবেন"],
         "answer": "আমি আমাদের কোর্স, ফি, সময়সূচি, ভর্তি ও কার্যক্রম সম্পর্কে তথ্য দিতে পারি। প্রশ্ন লিখুন, "
                   "আর প্রয়োজন হলে আমাদের টিমের সাথে যুক্ত করে দেব।",
         "answer_en": "I can share information about our courses, fees, schedules, admission and activities. Ask your question, and I'll connect you with our team if needed."},
        {"questions": ["are you there", "anyone there", "is anyone there", "hello anyone", "keu achen", "keu ache",
                       "apni achen", "কেউ আছেন", "কেউ আছে", "আছেন"],
         "answer": "জি, আমি আছি! বলুন, কীভাবে সাহায্য করতে পারি?",
         "answer_en": "Yes, I'm here! How can I help you?"},
        {"questions": ["thanks", "thank you", "thank u", "thanks a lot", "thank you so much", "thx", "tnx", "ty",
                       "dhonnobad", "dhonyobad", "ধন্যবাদ", "অনেক ধন্যবাদ", "jazakallah", "jazakallahu khairan"],
         "answer": "আপনাকেও ধন্যবাদ! আর কিছু জানতে চাইলে নির্দ্বিধায় জিজ্ঞেস করুন।",
         "answer_en": "You're welcome! Feel free to ask if there's anything else."},
        {"questions": ["ok", "okay", "okey", "k", "got it", "alright", "accha", "acha", "thik ache", "thik ase", "hmm",
                       "আচ্ছা", "ঠিক আছে", "ওকে", "জি"],
         "answer": "জি 🙂 আর কিছু জানতে চাইলে বলুন।",
         "answer_en": "Sure 🙂 Let me know if you'd like to know anything else."},
        {"questions": ["bye", "goodbye", "good bye", "see you", "allah hafez", "allah hafiz", "khoda hafez",
                       "pore kotha hobe", "আল্লাহ হাফেজ", "খোদা হাফেজ", "বিদায়", "পরে কথা হবে"],
         "answer": "ধন্যবাদ, ভালো থাকবেন! যেকোনো প্রয়োজনে আবার মেসেজ করবেন।",
         "answer_en": "Thank you, take care! Message us any time you need anything."},
        {"questions": ["good", "nice", "great", "awesome", "wow", "valo", "bhalo", "khub valo", "দারুণ", "ভালো", "চমৎকার"],
         "answer": "ধন্যবাদ! আর কোনো প্রশ্ন থাকলে জানাবেন।",
         "answer_en": "Thank you! Let me know if you have any other questions."},
    ],
    # --- retention: messages older than this are deleted; profiles and their saved details are kept
    "conversation_retention_days": 30,
    "context_version": 1,
    "embedding_provider": "hash",         # hash (offline, dev only) | openai | google
    "embedding_model": "",
}


async def get_setting(session: AsyncSession, key: str):
    row = await session.get(AppSetting, key)
    return row.value if row is not None else DEFAULTS.get(key)


async def set_setting(session: AsyncSession, key: str, value) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()
