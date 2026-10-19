# Live test: Messenger, Instagram, WhatsApp

প্রথম live test-এর আগে ও সময়ে হাতে-কলমে যাচাইয়ের তালিকা। প্রতিটা ধাপে ✅/❌ আর মন্তব্য লিখে রাখুন।
Dashboard-এর **Inbox** আর **Test chat → How this answer was made** প্যানেলে প্রতিটা উত্তরের path (greeting, smalltalk, qa_shortcut, variable, cache, llm, kb_extract, no_context) দেখা যায়।

## ০. শুরুর আগে

| # | যাচাই | কীভাবে |
|---|---|---|
| 0.1 | Stack চালু | `docker compose ps`: postgres, redis, backend, worker, frontend, cloudflared সব `Up` |
| 0.2 | Database migration | `docker compose exec backend alembic current` → সর্বশেষ head (এখন `0016`) |
| 0.3 | Admin login হয় | না হলে `docker compose exec backend python -m scripts.create_admin` |
| 0.4 | Public URL | `.env`-এর `PUBLIC_BASE_URL` আর `CORS_ORIGINS` = আসল HTTPS ঠিকানা। trycloudflare URL প্রতি restart-এ বদলায়: বদলালে `.env` আর Meta-র webhook URL দুটোই বদলান |
| 0.5 | Channel | **Channels**: Page/WhatsApp ID, access token, App secret, verify token সেভ করা; **Test connection** = Connected |
| 0.6 | AI | **AI Settings**: provider চালু, *Answer* route সেট; embedding `hash` নয় (Dashboard-এ embedding warning নেই) |
| 0.7 | Knowledge | **Knowledge base**-এ ফাইলগুলো `Ready`; **Q&A manager → Variables**-এ ফি ইত্যাদি সেট |
| 0.7b | Keyword index | **Knowledge base → Keyword index**: file text / Q&A / variables / programs-এর সংখ্যা ঠিক; একটা প্রশ্ন লিখে **Look up** চাপলে সঠিক record আসে। না এলে **Rebuild**। AI Settings → Model routing-এ *Keyword extraction* route দিলে কঠিন প্রশ্নে AI keyword বের করে |
| 0.8 | System health | Dashboard → **System health**: সব সবুজ; **AI Settings → Budget → Alerts**-এ webhook URL দিলে একটা test alert আসে কিনা দেখুন |

## ১. প্রতিটা channel-এ (Messenger, Instagram, WhatsApp) একই পরীক্ষা

একজন tester-এর **নিজের আসল account** থেকে Page / Instagram account / WhatsApp নম্বরে মেসেজ দিন।

| # | পাঠান | যা হওয়া উচিত |
|---|---|---|
| 1.1 | `hi` (প্রথমবার) | সাথে সাথে "seen" + typing bubble; তারপর "আসসালামু আলাইকুম! আমি Quantum Method AI Assistant…" |
| 1.2 | ৫ ঘণ্টার মধ্যে আবার `hello` | ছোট "জি, বলুন…" (কখনো "জানি না" নয়) |
| 1.3 | `Who are you?` | **শুধু ইংরেজিতে** small-talk উত্তর |
| 1.4 | `kemon achen` | **শুধু বাংলায়** উত্তর |
| 1.5 | বাংলায়: `কোর্স ফি কত?` | Variable/Q&A থেকে ফি (path `variable` বা `qa_shortcut`) |
| 1.6 | Banglish: `course fee koto?` | একই উত্তর, বাংলায় |
| 1.7 | English: `What is the course fee?` | একই ফি |
| 1.8 | `Yoga course fee koto?` (variable না থাকলে) | কোর্সের ফি **নয়**; সঠিক তথ্য না থাকলে "জানি না + agent লিখুন" |
| 1.9 | Knowledge-এর প্রশ্ন ঘুরিয়ে (যেমন `হার্ট ক্লাবে কি ডিম খাওয়া যায়?`) | ছোট, সঠিক উত্তর (path `llm` বা `kb_extract`); বানানো তথ্য নেই |
| 1.10 | অপ্রাসঙ্গিক: `আজ ঢাকার আবহাওয়া কেমন?` | "জানি না" ধরনের উত্তর, বানানো উত্তর নয় |
| 1.12 | ঘুরিয়ে / অন্য ভাষায়: `blood kothay pabo?`, `How can I join the course?` | Test chat → *How this answer was made* → keyword_search: কোন keyword, কোন record ও coverage দেখা যায়; উত্তর সেই record থেকে |
| 1.11 | Follow-up: আগে ফি জিজ্ঞেস, তারপর `ar schedule?` | আগের প্রসঙ্গ বুঝে উত্তর (Test chat-এ "question rewrite" দেখুন) |

## ২. একসাথে কয়েকটা মেসেজ (burst / debounce)

| # | পাঠান | যা হওয়া উচিত |
|---|---|---|
| 2.1 | ২–৩ সেকেন্ডের মধ্যে পরপর: `hi` · `course er` · `fee koto?` | typing bubble সাথে সাথে; **একটাই** উত্তর, তিনটা মিলিয়ে (ফি) |
| 2.2 | দুটো মেসেজ ১৫ সেকেন্ড ব্যবধানে | দুটো আলাদা উত্তর, সঠিক ক্রমে |
| 2.3 | burst-এর মাঝখানে `agent` | handover বার্তা যায়; পরের মেসেজে AI উত্তর দেয় না |

## ৩. Handover (মানুষের কাছে)

| # | কাজ | যা হওয়া উচিত |
|---|---|---|
| 3.1 | গেস্ট লেখেন `agent` বা `মানুষের সাথে কথা বলতে চাই` | handover বার্তা; Inbox-এ "AI is not replying" হলুদ banner; Dashboard-এর Handover queue-তে দেখা যায় |
| 3.2 | Staff Inbox থেকে উত্তর দেন | গেস্ট staff-এর উত্তর পান; AI চুপ থাকে |
| 3.3 | **Resume AI now** চাপুন, গেস্ট প্রশ্ন করুন | AI আবার উত্তর দেয় |
| 3.4 | Staff উত্তরের পর ৩০ মিনিট (বা সেট করা সময়) নীরব, তারপর গেস্টের প্রশ্ন | AI নিজে আবার চালু হয় |
| 3.5 | Inbox-এ কোনো AI উত্তরের পাশে **Wrong** চাপুন | "cached copy blocked"; Knowledge → Saved AI answers-এ সেটা Blocked |

## ৪. ২৪ ঘণ্টার window

| # | কাজ | যা হওয়া উচিত |
|---|---|---|
| 4.1 | গেস্টের শেষ মেসেজের ২৪ ঘণ্টা পর Inbox থেকে উত্তর (Messenger/Instagram) | পাঠানো হয় না; "outside messaging window" badge (human-agent tag চালু থাকলে ৭ দিন পর্যন্ত যায়) |
| 4.2 | একই কাজ WhatsApp-এ | template চাওয়া হয়; approved template পাঠালে যায় |
| 4.3 | Inbox header | বাকি সময় / "Window closed" ঠিকমতো দেখায় |

## ৫. "আমার তথ্য মুছে ফেলুন" ও consent

| # | পাঠান | যা হওয়া উচিত |
|---|---|---|
| 5.1 | ২–৩টা প্রশ্নের পর consent প্রশ্ন এলে `হ্যাঁ` | consent দেওয়া; Profile-এ Consent given |
| 5.2 | ফোন নম্বর লিখুন (consent-এর আগে) | Profile-এ "Waiting for consent" (এনক্রিপ্ট করা); consent-এর পর সেভ |
| 5.3 | `delete my data` / `আমার তথ্য মুছে ফেলুন` | নিশ্চিতকরণ বার্তা; Profile-এর নাম/ফোন/তথ্য মুছে গেছে; আগের মেসেজের লেখা "[deleted at user's request]" |
| 5.4 | (Intent router চালু থাকলে) `আমার সব কিছু ভুলে যান` | আগে "হ্যাঁ লিখুন" জিজ্ঞেস করে; `হ্যাঁ`-র পরেই মোছে, অন্য কিছু লিখলে মোছে না |

## ৬. Profile ও identity

| # | কাজ | যা হওয়া উচিত |
|---|---|---|
| 6.1 | একই মানুষ Messenger-এ নম্বর দিয়ে, পরে সেই নম্বরের WhatsApp থেকে মেসেজ | একই profile (নাম মিললে); না মিললে Profiles-এ "may have more than one profile" |
| 6.2 | Profiles list | Test chat-এর profile দেখায় না; গেস্টের নাম ও ছবি দেখায় |

## ৭. Tools (চালু করলে)

| # | পাঠান | যা হওয়া উচিত |
|---|---|---|
| 7.1 | `ami meditation course e bhorti hote chai` | "আমি এটি করতে চাই: … ঠিক আছে?"; `হ্যাঁ`-র পর ধন্যবাদ বার্তা; Inbox-এ "Note for staff: New lead" |
| 7.2 | একই, কিন্তু `না` | কিছুই হয় না |
| 7.3 | `amake call diben kal bikale` | নিশ্চিতকরণ; `হ্যাঁ`-র পর handover + "Callback requested" নোট |

## ৮. শেষে

- Dashboard → System health: queue খালি, failed send নেই, AI error rate স্বাভাবিক।
- `docker compose logs worker --since 1h | grep -i error` → অপ্রত্যাশিত error নেই।
- Evaluation পেজে eval set চালিয়ে accuracy লিখে রাখুন (পরের পরিবর্তনের সাথে তুলনার জন্য)।

## Meta-র docs-এর সাথে মিলিয়ে দেখা (অক্টোবর ২০২৬)

- **Instagram send**: কোডে `POST graph.facebook.com/<ver>/me/messages`, Page access token দিয়ে। এটা *Facebook Login for Business* (Page-এর সাথে যুক্ত Instagram business account) পথের সাথে মেলে। *Business Login for Instagram* (`graph.instagram.com`, Instagram token) এই কোডে সমর্থিত নয়।
- **Messenger window error**: docs-এ দুটো আছে, `10 / 2018278` এবং `10 / 2534022` ("outside of allowed window")। কোডে এখন দুটোই ধরা হয় (`channels/meta_messaging.py`)।
- **WhatsApp window error**: `131047` (re-engagement, ২৪ ঘণ্টা পার) → template লাগবে। কোডের সাথে মেলে।
- **Retry করার code** (`channels/graph.py`): `1`, `2` (সাময়িক error), `4`, `17`, `32`, `613` (rate limit), `80007`, `130429`, `131056` (WhatsApp rate/throughput)। সবই docs-এ সাময়িক বা rate-limit হিসেবে আছে, তাই backoff দিয়ে retry ঠিক আছে।
- Docs: [Messenger error codes](https://developers.facebook.com/docs/messenger-platform/error-codes) · [Instagram send message](https://developers.facebook.com/docs/messenger-platform/instagram/features/send-message) · [WhatsApp error codes](https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes)
