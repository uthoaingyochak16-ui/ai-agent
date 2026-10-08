# উন্নতির জন্য AI Prompt তালিকা

প্রতিটা prompt আলাদাভাবে AI coding tool-এ (Claude Code, Cursor ইত্যাদি) দিন, **একটা একটা করে, ক্রম অনুযায়ী**।
Prompt-গুলো ইংরেজিতে লেখা, কারণ coding AI ইংরেজি নির্দেশ সবচেয়ে নিখুঁতভাবে মানে। প্রতিটার উপরে বাংলায় ব্যাখ্যা আছে।

## শুরু করার আগে (জরুরি)
1. এই repo-তে এখনো একটাও git commit নেই। আগে সব commit করুন, যাতে কিছু ভাঙলে ফেরত যেতে পারেন:
   ```bash
   git add -A && git commit -m "Baseline before improvements"
   ```
   `.env` যেন commit না হয়, সেটা `.gitignore`-এ আছে কিনা দেখে নিন।
2. প্রতিটা prompt শেষ হলে `cd backend && python -m pytest -q` চালান। সব test পাস করলে commit করুন, তারপর পরের prompt।
3. প্রতিটা prompt-এর শুরুতে নিচের "Common preamble" অংশটা জুড়ে দিন।

---

## Common preamble (প্রতিটা prompt-এর আগে জুড়ে দিন)

```text
Context: This is a multi-channel AI customer assistant (Messenger, Instagram, WhatsApp) for a Bangladeshi
organisation. Backend: FastAPI + SQLAlchemy async + Postgres 16/pgvector + Redis + arq worker (backend/app).
Frontend: React + Vite + Tailwind (frontend/src). Customers write in Bangla, English and Banglish.
Message flow: api/webhooks.py -> WebhookEvent row -> worker.py process_event_job -> services/ingest.py
handle_message -> _respond -> services/reply_engine.py -> services/pipeline.py run_pipeline.
Settings live in services/settings_service.py DEFAULTS and are editable in the AI Settings page.

Rules for this change:
- Read the relevant files fully before editing. Match the existing code style, naming and comment density.
- Keep the privacy layer intact: every LLM request must still go through PrivacyGuard / GuardedRequest.
- Any new setting goes into DEFAULTS with a sensible default and, if admins should tune it, into AISettings.tsx.
- Schema changes need a new Alembic migration after 0008 (alembic check must pass).
- Add or update tests in backend/tests (pytest, external HTTP mocked with respx). All existing tests must pass.
- Do not change unrelated behaviour. At the end, summarise what changed and anything you could not verify.
```

---

## Prompt 1: একই conversation-এর message একটার পর একটা process করা (সবচেয়ে জরুরি)

**কেন:** এখন একজন user পরপর ৩টা message দিলে worker সেগুলো একসাথে চালায় (`max_jobs=20`)। ফলে ৩টা আলাদা উত্তর যায়, কখনো উল্টো ক্রমে।

```text
Task: Guarantee that messages of the same conversation are processed strictly one at a time, in arrival order.

Problem today: worker.py runs up to 20 jobs in parallel. services/ingest.py resolve() takes a FOR UPDATE lock on
the ChannelIdentity row, but _respond() commits several times, so the lock is released before the LLM call and a
second message from the same person is processed concurrently. Its reply is sent without knowing the first
reply, and replies can arrive out of order.

Implement:
1. In services/ingest.py process_event, after loading the event, take a Postgres session-level advisory lock keyed
   on (channel, external_user_id) of the incoming message (e.g. pg_advisory_lock(hashtextextended(...)) on a
   dedicated connection), held for the whole handle_message call and always released in a finally block.
   It must survive the multiple commits inside _respond.
2. If the lock cannot be acquired within a short timeout (e.g. 60 s), put the event back to 'queued' without
   counting it as a failed attempt, so the requeue cron retries it.
3. Process events of the same person in WebhookEvent.id order: before handling, if an older event of the same
   channel+external user is still 'queued' or 'processing', defer this one (re-enqueue with a small delay) instead
   of handling it.
4. Tests: two events for the same user enqueued together are processed sequentially (second sees first's assistant
   reply in history); events of different users still run in parallel; a crash releases the lock.
```

---

## Prompt 2: পরপর আসা message জুড়ে একটা উত্তর (debounce)

**কেন:** মানুষ "hi", "course er", "fee koto?" এভাবে ভেঙে লেখে। বড় কোম্পানিগুলো কয়েক সেকেন্ড অপেক্ষা করে সব একসাথে বুঝে একটা উত্তর দেয়। এতে খরচও কমে।
(Prompt 1 শেষ হওয়ার পরে দিন।)

```text
Task: Add message debouncing so a burst of messages from one person gets ONE combined reply.

Implement:
1. New settings in DEFAULTS: "debounce_seconds" (default 3, 0 = off) and "debounce_max_seconds" (default 10).
2. In api/webhooks.py, enqueue process_event_job with _defer_by=debounce_seconds (arq supports deferred jobs).
3. In services/ingest.py, when a job starts, check whether a NEWER event from the same channel+external user
   arrived. If yes and the oldest pending event is younger than debounce_max_seconds, store this message
   (user Message row as today) but skip replying; the newest event's job replies.
4. The replying job builds the question from all consecutive user messages since the last assistant/agent reply,
   joined with newlines, and passes that combined text to _respond / generate_reply. Erasure requests, consent
   replies and handover keywords must still be detected on EACH individual message.
5. The typing indicator should start as soon as the first message arrives, not after the debounce delay.
6. Test chat (api/test_chat.py) must not be debounced.
7. Tests: 3 messages within 2 s -> exactly one assistant reply containing context from all three;
   messages 15 s apart -> separate replies; debounce_seconds=0 restores current behaviour.
```

---

## Prompt 3: Bangla-র জন্য উপযুক্ত embedding নিশ্চিত করা

**কেন:** default `hash` embedder production-এ দুর্বল। ভুল embedding থাকলে retrieval আর cache দুটোই ভুল করে।

```text
Task: Make the embedding setup production-safe for Bangla/Banglish.

Implement:
1. On backend startup and in the dashboard (Dashboard.tsx and AI Settings > Retrieval), show a clear warning banner
   when embedding_provider is "hash" and at least one real channel (not test) is configured.
2. Store the embedding provider+model+dimension that produced each vector: add columns (or a single
   "embedding_version" setting compared with a per-row value) on Chunk, QAPair and SemanticCache. When the admin
   changes the embedding provider/model, show "N items need re-embedding" and add a "Re-embed all" button that runs
   as an arq background job (batched, resumable, progress visible). Bump context_version when done so the cache resets.
3. Add an optional self-hosted provider "local" that calls an OpenAI-compatible /v1/embeddings endpoint at a
   configurable base_url (so a self-hosted bge-m3 can be used for privacy), reusing _HttpEmbedder in rag/embeddings.py.
4. Tests: mismatch detection, re-embed job updates all rows, warning appears only for hash + real channel.
```

---

## Prompt 4: Evaluation system (উত্তর কতটা ঠিক হচ্ছে তা মাপা)

**কেন:** এখনকার test কোড যাচাই করে, উত্তরের মান মাপে না। পরের সব পরিবর্তন (reranker, router) ভালো হলো কিনা বোঝার জন্য এটা আগে লাগবে।

```text
Task: Build an answer-quality evaluation harness and dashboard.

Implement:
1. New table eval_cases (question, expected_answer, expected_path optional, language, tags, active, created_at)
   with an Alembic migration, plus CRUD API under /api/eval and a new "Evaluation" page in the frontend.
2. "Add to eval set" buttons: in Test chat after "Answer is wrong" correction, and on items in the Unanswered list.
   Also a CSV import (question, expected_answer, language).
3. An eval run (arq job) that sends every active case through run_pipeline with persist=False, is_test=True,
   and stores per case: actual answer, path, latency, tokens, cost, retrieved chunk ids.
4. Scoring: use the cheapest configured LLM route as a judge (through PrivacyGuard; questions are admin-written)
   returning JSON {correct: bool, grounded: bool, reason}. Cases whose path is no_context while an expected
   answer exists count as "missed".
5. Eval run results page: overall accuracy, missed rate, hallucination (not grounded) rate, per-language breakdown,
   cost, and a diff view against the previous run. Store the settings snapshot (thresholds, models, prompt hash)
   with each run so results can be compared.
6. Tests with a fake judge provider.
```

---

## Prompt 5: Retrieval-এ reranker যোগ করা

**কেন:** [rag/search.py](backend/app/rag/search.py)-তে হাতে ঠিক করা weight (`0.2*vec + 0.2*lx + 0.35*wcov ...`) ভঙ্গুর। Reranker model এর চেয়ে অনেক বেশি নির্ভুল।

```text
Task: Add an optional cross-encoder reranking step to hybrid_search in backend/app/rag/search.py.

Implement:
1. A Reranker interface (rag/rerank.py) with providers: "none" (current behaviour), "cohere"
   (rerank-multilingual model via HTTP), and "local" (OpenAI/TEI-compatible /rerank endpoint at a base_url, e.g. a
   self-hosted bge-reranker-v2-m3). Keys stored encrypted like other providers; outgoing text goes through the
   existing redaction for the provider's allow_pii policy.
2. Settings: rerank_provider (default "none"), rerank_model, rerank_candidates (default 20),
   rerank_min_score (default tuned per provider, editable).
3. In hybrid_search: gather candidates as today (vector top 40 + lexical top 40), keep the current relevance
   filter as a cheap pre-filter, then if a reranker is configured send the top rerank_candidates to it and order by
   its score; drop results below rerank_min_score. The final "score" used for escalate_below must then be the
   reranker score. If the reranker call fails, fall back silently to the current scoring and log it.
4. Show the rerank score in the Test chat debug panel next to the existing vector/keyword numbers.
5. Tests with respx mocks; run the eval set (Prompt 4) before/after and report the difference.
```

---

## Prompt 6: Follow-up প্রশ্ন ঠিকমতো বোঝা (query rewriting)

**কেন:** এখন ৪ শব্দের কম হলে শুধু আগের message জুড়ে দেওয়া হয়। "oita koto?", "ar weekend e?" ধরনের প্রশ্নে retrieval ভুল হয়।

```text
Task: Add standalone-question rewriting before retrieval in services/pipeline.py.

Implement:
1. New LLM task "rewrite" (route configurable in AI Settings > Model routing, small/cheap model, max_tokens ~80,
   temperature 0). Prompt: given the last few turns and the new message, output ONLY the self-contained question in
   the user's language; if the message is already self-contained, return it unchanged.
2. Call it only when needed: there is prior history AND (the message is short, or contains pronouns/references like
   oita, eta, seta, oi, ঐটা, এটা, সেটা, it, that, this, also, ar, আর). Build the request through PrivacyGuard
   with history redaction, like the answer task.
3. Use the rewritten question for exact/near Q&A matching, embedding, cache lookup and retrieval; keep the original
   text for the final answer prompt and for storage. Replace the current "len(text.split()) < 4" concatenation.
4. If no rewrite route is configured or it fails, keep today's behaviour.
5. Show original vs rewritten question in the Test chat debug panel and log the call in LLMCall with task="rewrite".
6. Tests with a fake provider; add follow-up cases to the eval set.
```

---

## Prompt 7: Regex-এর বদলে একটা সস্তা intent router

**কেন:** greeting, smalltalk, handover, consent আলাদা আলাদা regex আর word list দিয়ে ধরা হয়, যা ভঙ্গুর। একটা ছোট model-এর একটা call দিয়ে সব একসাথে বোঝা যায়।

```text
Task: Add an LLM intent router that runs before the answer pipeline, with the current rules as fast path and fallback.

Implement:
1. New LLM task "router" (cheap model, temperature 0, JSON output). Output schema:
   {intent: "greeting"|"smalltalk"|"question"|"handover_request"|"consent_yes"|"consent_no"|"erasure_request"|
   "complaint"|"other", language: "bn"|"en"|"banglish", needs_human: bool, standalone_question: string|null}.
   If Prompt 6 is done, merge the rewrite into this call (standalone_question) to save one call.
2. Order in ingest._respond: keep the cheap deterministic checks first (exact greeting word, exact handover keyword,
   exact Q&A match) so most messages still cost 0 tokens; call the router only when none of those matched.
3. Router decisions map onto the existing functions (greeting_reply, smalltalk_reply, pause_ai, record_consent,
   erase_profile). For erasure_request coming from the router (not the exact keyword), ask the user to confirm
   ("reply YES to delete") before erasing.
4. needs_human=true or intent=complaint -> hand over using the existing handover flow and handover_ack text.
5. Setting "router_enabled" (default false) so it can be switched on after checking the eval set.
6. If the router fails or is not configured, fall back to today's regex logic.
7. Tests: each intent maps to the right action; router failure falls back; consent/erasure safety.
```

---

## Prompt 8: Semantic cache নিরাপদ করা

**কেন:** cache সব user-এর মধ্যে ভাগ হয়। "fee koto?" আর "yoga course fee koto?" কাছাকাছি ধরা পড়লে ভুল উত্তর সবার কাছে যেতে পারে।

```text
Task: Make the semantic cache in services/pipeline.py safer against serving a wrong answer to everyone.

Implement:
1. Before serving a cache hit, require that the cached question and the new question have the same set of
   topic words (reuse content_words / NEUTRAL_WORDS / _matches from rag/search.py and rag/variables.py the same way
   the Q&A coverage check does). Different topic words -> no hit, even with high vector similarity.
2. Add a "status" column to SemanticCache: "auto" | "verified" | "blocked". Only serve "auto" entries if
   cache_threshold is met AND the entry has been served without negative feedback; always serve "verified".
3. Admin page section (Knowledge or AI Settings): list cache entries with hits, edit/verify/delete buttons.
4. When an admin marks an answer wrong in Test chat or Inbox, delete or block cache entries whose answer equals
   that reply, and bump context_version only if the admin chooses "clear all cache".
5. Variables changes (rag/variables.py) must invalidate cache entries that contain the old value.
6. Tests for topic-word mismatch, verified/blocked behaviour and invalidation.
```

---

## Prompt 9: Q&A matching দ্রুত করা (scale)

**কেন:** প্রতিটা message-এ সব Q&A দুইবার Python loop-এ `SequenceMatcher` দিয়ে মেলানো হয়। Q&A বাড়লে প্রতিটা উত্তর ধীর হবে।

```text
Task: Remove the per-message full scan of all QAPair rows in services/pipeline.py (steps 1a' and the coverage loop).

Implement:
1. Load active QA pairs once into an in-process index keyed by context_version (same pattern as lexical_index in
   rag/search.py), holding precomputed search_norm(question) and content_words(question).
2. Use Postgres pg_trgm (migration: CREATE EXTENSION IF NOT EXISTS pg_trgm, GIN trigram index on a normalized
   question column) to fetch the top ~20 candidates by trigram similarity, then run SequenceMatcher/coverage
   only on those candidates.
3. Make sure saving/deleting a Q&A bumps context_version (or a separate qa_version) so the index refreshes.
4. Keep results identical to today for existing tests; add a test with 5,000 QA pairs asserting pipeline step 1
   stays under a reasonable time budget.
```

---

## Prompt 10: Observability ও alert

**কেন:** live চালু হলে সমস্যা আগে থেকে ধরা দরকার: queue জমে যাওয়া, Meta send ব্যর্থ হওয়া, budget শেষ হওয়া।

```text
Task: Add operational monitoring.

Implement:
1. A /api/health/deep endpoint (admin only) reporting: queued/processing/failed WebhookEvent counts and oldest age,
   messages with delivery_status failed/blocked in the last 24 h, LLM error rate and p95 latency from LLMCall,
   budget level, embedding provider sanity.
2. Dashboard.tsx: a "System health" card showing those numbers with red/amber states, plus key AI metrics:
   resolution rate (conversations with AI replies and no handover), handover rate, no_context rate, avg cost per
   conversation, median reply latency (user message -> assistant message).
3. Alerts: a cron in worker.py every 5 min that, when thresholds are crossed (events stuck > 5 min, failed sends
   > N/hour, budget alert), sends a notification to a configurable webhook URL (Slack/Telegram-compatible JSON).
   Rate-limit alerts to one per issue per hour.
4. Structured JSON logs include conversation_id and event_id on every log line of the message flow.
5. Tests for the metrics queries and alert de-duplication.
```

---

## Prompt 11 (ঐচ্ছিক, পরে): AI দিয়ে কাজ করানো (tools/actions)

**কেন:** শুধু প্রশ্নের উত্তর না দিয়ে lead নেওয়া, কোর্সে ভর্তি বা appointment বুকিং করানো। বড় কোম্পানির AI agent এখন এদিকেই যাচ্ছে।

```text
Task: Add tool-calling support so the assistant can perform simple actions, starting with "create_lead" and
"request_callback".

Implement:
1. Extend llm/providers.py adapters (Anthropic, OpenAI, Google) to accept tool definitions and return tool calls,
   keeping GuardedRequest as the only input type; tool arguments returned by the model pass through the output
   filter and placeholder restoration before use.
2. A tool registry (services/tools.py): each tool has a JSON schema, a handler, and a flag "requires_confirmation".
   create_lead stores name/phone/interest on the profile (respecting consent rules in services/consent.py) and
   notifies staff in the Inbox; request_callback creates a handover with a reason and preferred time.
3. Admin can enable/disable each tool in AI Settings. Max 2 tool rounds per message; every tool call is logged.
4. The assistant must confirm with the user before any tool with requires_confirmation runs.
5. Tests with a fake provider returning tool calls.
```

---

## শেষ ধাপ: live test-এর আগে checklist

```text
Task: Prepare for the first live test with Meta and a real LLM. Do not change code unless a check fails.
1. Verify against Meta's current Graph API docs: the Instagram send endpoint used in channels/meta_messaging.py,
   window error codes in channels/ (10/2018278, 131047), and the retryable codes in channels/graph.py.
   List any mismatches with doc links.
2. Run the Docker stack (docker compose up --build) and report any errors; it has never been run in Docker.
3. Check that .env is git-ignored, secrets are not logged, and CORS_ORIGINS / PUBLIC_BASE_URL are set for production.
4. Write a short LIVE_TEST.md with step-by-step manual test cases per channel (Bangla, English, Banglish questions,
   burst messages, handover, 24 h window, delete my data).
```
