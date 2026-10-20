from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting
from app.services.message_defaults import MESSAGE_DEFAULTS

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
    "ai_auto_resume_minutes": 30,
    # --- intent router (LLM task "router"): understands requests the word lists miss; off until checked on the eval set
    "router_enabled": False,
    "team_instructions": [],              # rules staff teach in Test Chat (services/team_instructions.py)
    "erasure_confirm_prompt": "আপনার সব ব্যক্তিগত তথ্য মুছে ফেলতে চান? নিশ্চিত হলে \"হ্যাঁ\" লিখুন।",
    "erasure_confirm_prompt_en": "Do you want all your personal data deleted? Reply YES to confirm.",
    "consent_yes_ack": "ধন্যবাদ! আপনার তথ্য সংরক্ষণ করা হবে। আর কিছু জানতে চাইলে বলুন।",
    "consent_yes_ack_en": "Thank you! Your details will be saved. Let me know if you need anything else.",
    "consent_no_ack": "ঠিক আছে, আপনার তথ্য সংরক্ষণ করা হবে না। আর কিছু জানতে চাইলে বলুন।",
    "consent_no_ack_en": "Okay, your details will not be saved. Let me know if you need anything else.",
    # --- tools (the assistant can act). create_lead / request_callback ask the guest to confirm first;
    # current_datetime only reads the clock (Bangladesh time), so it runs inline and is on by default.
    "tools_enabled": {"create_lead": True, "request_callback": False, "current_datetime": True, "search_knowledge": True},
    "tool_confirm_prompt": "আমি এটি করতে চাই: {action}। ঠিক আছে? \"হ্যাঁ\" বা \"না\" লিখুন।",
    "tool_confirm_prompt_en": "I'd like to do this for you: {action}. Is that okay? Reply YES or NO.",
    "tool_declined_ack": "ঠিক আছে, কিছু করা হয়নি। আর কিছু জানতে চাইলে বলুন।",
    "tool_declined_ack_en": "Okay, nothing was done. Let me know if you need anything else.",
    "tool_lead_ack": "ধন্যবাদ! আপনার আগ্রহের কথা আমরা জানলাম, আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে।",
    "tool_lead_ack_en": "Thank you! We've noted your interest and our team will contact you soon.",
    "tool_callback_ack": "ঠিক আছে! আমাদের একজন প্রতিনিধি আপনাকে কল করবেন (সময়: {time})।",
    "tool_callback_ack_en": "Done! A team member will call you (time: {time}).",
    # --- monitoring alerts (worker cron every 5 min, one message per issue per hour)
    "alert_webhook_url": "",              # Slack incoming webhook, or Telegram .../sendMessage?chat_id=...; empty = off
    "alert_stuck_minutes": 5,             # an incoming message waiting longer than this = worker problem
    "alert_failed_sends_per_hour": 5,     # more failed replies than this in an hour = channel problem
    "debounce_seconds": 0,                # wait this long for more messages from the same guest, then reply once; 0 = off
    "debounce_max_seconds": 10,           # ...but never hold a burst longer than this         # a chat paused by staff goes back to the AI after this much staff silence; 0 = never     # Messenger/IG HUMAN_AGENT tag (7-day window for human replies)
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
    # If enabled, questions unrelated to the organisation's knowledge base may be answered with the model's
    # general knowledge.  The fallback prompt explicitly forbids treating that knowledge as company information.
    "general_answer_enabled": False,
    # --- retrieval / pipeline
    "top_k": 6,                           # candidate records (keyword + meaning search) the answer step reads and judges
    # --- keyword index: guest question -> keywords -> index -> only the matching records go to the answer step
    "keyword_search_enabled": True,
    "keyword_ai_enabled": True,           # an AI call turns the question into Bangla + English keywords (needs the "keywords" route)
    "keyword_ai_skip_above": 0.9,         # free rule-based keywords already cover this much of the question: no AI call; 0 = always ask the AI
    "keyword_min_coverage": 0.5,          # a record must cover at least this share of the question's (rarity-weighted) keywords
    "keyword_top_k": 4,                   # records handed to the answer step
    "chunk_words": 120,                   # knowledge-base chunk size (words); re-process documents after changing
    "chunk_overlap_words": 20,
    "max_answer_chars": 450,              # replies are kept short (AI instructed; file/Q&A text trimmed); 0 = no limit
    "qa_text_threshold": 0.86,            # Q&A shortcut by plain wording similarity (typos, spelling variants)
    "history_len": 4,
    "chunk_token_budget": 2000,           # Bangla text is token-heavy: room for every candidate record
    "qa_threshold": 0.92,
    "cache_threshold": 0.95,
    "cache_ttl_days": 14,
    "retrieval_min_similarity": 0.25,
    "escalate_below": 0.45,
    "embedding_provider": "hash",
    "embedding_model": "hash-ngram-v1",
    "rerank_provider": "none",
    "rerank_model": "rerank-multilingual-v3.0",
    "rerank_candidates": 20,
    "rerank_min_score": None,            # auto: Cohere 0.2, local 0.0; set a number to override
    "embedding_reembed_progress": {"status": "idle", "done": 0, "total": 0},
    "no_answer_reply": "দুঃখিত, এই বিষয়ে আমার কাছে এখন সঠিক তথ্য নেই। আপনি চাইলে আমাদের একজন প্রতিনিধির সাথে যুক্ত করে দিতে পারি — শুধু \"agent\" লিখুন।",
    "no_answer_reply_en": "Sorry, I don't have accurate information on that right now. I can connect you with a team member — just type \"agent\".",
    "consent_prompt": "আপনাকে আরও ভালোভাবে সাহায্য করতে আপনার নাম ও যোগাযোগের তথ্য সংরক্ষণ করতে পারি? হ্যাঁ/না লিখুন (যেকোনো সময় \"আমার তথ্য মুছে ফেলুন\" লিখতে পারেন)।",
    "consent_prompt_en": "By the way, may I save your name and contact details to serve you better? Reply yes or no (you can say 'delete my data' any time).",
    # --- guest details: full name and phone, asked ONCE in a guest's first meaningful conversation; not given ->
    # never asked again (services/lead.py, Profile.personal_info_request_status).
    # {fields} = the details still missing, {example} = a sample answer for them. Giving them after this explanation
    # counts as consent to save them. The chat never says "saved"; only a guest who asks is told (data_saving_reply).
    "lead_capture_enabled": False,
    # every guest message is split into pieces by keywords; pieces that may hold personal details go to the AI
    # ("extraction" route), which saves what is guest info to the profile (services/info_detect.py, post_reply.py)
    "info_collection_enabled": True,
    "reply_max_lines": 5,  # a longer reply is sent as several messages of at most this many lines (0 = never split)
    "lead_prompt": "আপনার সাথে যোগাযোগ রাখা এবং প্রয়োজন হলে সাহায্য করার জন্য আপনার {fields} লাগতে পারে। দিতে চাইলে লিখে দিন (যেমন: {example})। না দিতে চাইলেও কোনো সমস্যা নেই।",
    "lead_prompt_en": "To stay in touch and help you when needed, we may need your {fields}. If you'd like, please share them (e.g. {example}). It's fine if you'd rather not.",
    "lead_declined_ack": "ঠিক আছে, কোনো সমস্যা নেই।",
    "lead_declined_ack_en": "No problem at all.",
    "lead_explain": "আপনার সাথে যোগাযোগ রাখা এবং আপনাকে আরও ভালোভাবে সাহায্য করার জন্য আপনার {fields} প্রয়োজন। অনুগ্রহ করে এভাবে লিখে দিন: {example}",
    "lead_explain_en": "We need your {fields} so we can stay in touch and help you better. Please write it like this: {example}",
    "data_saving_reply": "জি, আপনার সাথে যোগাযোগ রাখা এবং আপনাকে আরও ভালোভাবে সাহায্য করার জন্য আপনার দেওয়া তথ্য আমরা সংরক্ষণ করি। এগুলো শুধু এই কাজেই ব্যবহার হয়। চাইলে যেকোনো সময় \"আমার তথ্য মুছে ফেলুন\" লিখে মুছে ফেলতে পারেন।",
    "data_saving_reply_en": "Yes. We keep the details you share so we can stay in touch and help you better. They are used only for that. You can write \"delete my data\" at any time to remove them.",
    "lead_thanks": "ধন্যবাদ!",
    "lead_thanks_en": "Thank you!",
    "lead_complete": "ধন্যবাদ! আপনি কি আর কিছু জানতে চান?",
    "lead_complete_en": "Thank you! Is there anything else you would like to know?",
    "lead_anything_else": "আপনি কি আর কিছু জানতে চান?",
    "lead_anything_else_en": "Is there anything else you would like to know?",
    # --- program follow-up (services/program_followup.py): after a program / course / event answer, once per
    # conversation, as a separate message; never when the profile already has a location. {program_name} is put in
    # quotation marks automatically; programs are matched to the guest's district exactly (no "nearest" guess).
    "program_followup_enabled": True,
    "program_district_prompt": "আপনি কোন জেলায় থাকেন? জানালে আপনার কাছাকাছি কোনো আয়োজন থাকলে জানাতে পারব।",
    "program_district_prompt_en": "Which district do you live in? If there is an event near you, I can let you know.",
    "program_nearby_reply": "আপনার জেলার কাছাকাছি আমাদের {program_name} আয়োজনটি {place}-এ হবে। চাইলে আপনি সেখানে অংশ নিতে পারেন।",
    "program_nearby_reply_en": "Near your district, our {program_name} will take place at {place}. You are welcome to join it.",
    "program_nearby_none": "আপনার কাছাকাছি কোনো নিশ্চিত আয়োজন এখন তালিকায় নেই। চাইলে আমাদের team আপনাকে পরবর্তী আয়োজন সম্পর্কে জানাতে পারে।",
    "program_nearby_none_en": "There is no confirmed event near you on the list right now. If you like, our team can let you know about the next one.",
    # --- reply style per question type (services/reply_style.py): {type: {tone, template(_en), followup(_en), handover}}
    "reply_styles": {},
    "handover_offer": "চাইলে আমাদের একজন প্রতিনিধির সাথে যুক্ত করে দিতে পারি — শুধু \"agent\" লিখুন।",
    "handover_offer_en": "If you like, I can connect you with a team member — just type \"agent\".",
    # --- card details are never asked for (services/security.py)
    "card_warning": "নিরাপত্তার জন্য আমরা কখনো কার্ড নম্বর, পিন বা OTP চাই না। অনুগ্রহ করে এগুলো কাউকে দেবেন না।",
    "card_warning_en": "For your safety, we never ask for card numbers, PINs or OTPs. Please don't share them with anyone.",
    # --- greetings (bare "hi"/"hello"/"salam": canned reply, no AI call)
    "greeting_enabled": True,
    "greeting_first_reply": "আসসালামু আলাইকুম! আমি Quantum Method AI Assistant। আমি কীভাবে আপনাকে সাহায্য করতে পারি?",
    "greeting_return_reply": "আসসালামু আলাইকুম! আমি কীভাবে আপনাকে সাহায্য করতে পারি?",
    "greeting_repeat_reply": "জি, বলুন — আমি কীভাবে সাহায্য করতে পারি?",  # another "hi" inside the gap
    # formal English twins, for a guest who has been writing English ("নমস্কার"/"আদাব" open the reply in kind)
    "greeting_first_reply_en": "Assalamu Alaikum! I am the Quantum Method AI Assistant. How may I help you?",
    "greeting_return_reply_en": "Assalamu Alaikum! How may I help you?",
    "greeting_repeat_reply_en": "Yes, please go ahead. How may I help you?",
    "greeting_gap_hours": 5,
    "greeting_words": [],               # from app/defaults/messages_and_handover.json (applied below)
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
         "answer": "জি, ঠিক আছে। আর কিছু জানতে চাইলে অনুগ্রহ করে জানাবেন।",
         "answer_en": "Certainly. Please let me know if there is anything else you would like to know."},
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
    # --- public website program schedule sync (daily worker + manual Program names button)
    "program_sync_enabled": True,
    "program_sync_url": "https://event.quantummethod.org.bd/api/event?language=bn",
    "program_sync_status": {"status": "idle", "source_url": "https://event.quantummethod.org.bd/api/event?language=bn"},
}
# Everything on AI Settings -> "Messages and handover" and "Small talk" (greetings, small talk, handover, consent,
# lead and system replies, the system prompt) comes from one file: app/defaults/messages_and_handover.json.
# It replaces the values written above for those keys. CODE_BEHAVIOUR_DEFAULTS keeps the original switches (lead
# capture, router, timings, system prompt) so the test suite checks the logic independently of one deployment's file.
_BEHAVIOUR_KEYS = ("lead_capture_enabled", "info_collection_enabled", "router_enabled", "system_prompt", "debounce_seconds",
                   "debounce_max_seconds", "reply_max_lines", "ai_auto_resume_minutes", "conversation_retention_days",
                   "greeting_gap_hours", "greeting_enabled", "smalltalk_enabled")
CODE_BEHAVIOUR_DEFAULTS = {k: DEFAULTS[k] for k in _BEHAVIOUR_KEYS if k in DEFAULTS}
DEFAULTS.update(MESSAGE_DEFAULTS)


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
