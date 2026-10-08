# Multi-channel AI Assistant (Messenger, Instagram, WhatsApp)

Replies on behalf of a Facebook Page, an Instagram account and a WhatsApp number from your own knowledge base, keeps ONE
profile per person across channels, and filters everything between your database and the external AI.
Admin dashboard included (Inbox, Profiles, Knowledge base, Q&A, Channels, AI settings, **Test chat** with a debug panel).

## Status

All phases are implemented. 149 automated tests pass (Postgres 16 + pgvector + Redis, external HTTP mocked); the dashboard
type-checks, builds, and was exercised in Chromium against the live API with a stand-in LLM.
**Not yet done by anyone: a live test against Meta and a real LLM provider** (see "Before going live").

## Quick start (Docker)

```bash
cp .env.example .env
(cd backend && python -m scripts.gen_keys)        # paste output into .env (SECRET_KEY, ENCRYPTION_KEY, HMAC_KEY, META_VERIFY_TOKEN)
docker compose up --build -d                       # postgres+pgvector, redis, backend (runs migrations), worker, dashboard
docker compose exec backend python -m scripts.create_admin --email you@example.com --password 'a-long-password'
docker compose exec backend python -m scripts.seed # optional: sample Q&A, variables, document, profile, default routes
open http://localhost:8080                         # dashboard; /api and /webhooks are proxied on the same host
```
The Docker files were written but could not be run where this was built (no Docker available); the same stack was run natively.

Without Docker: Postgres 16 with `vector`, Redis, Python 3.12, Node 20+.
```bash
cd backend && pip install -r requirements.txt && cp ../.env.example .env   # fill in
alembic upgrade head
uvicorn app.main:app --reload &  arq app.worker.WorkerSettings &
cd ../frontend && npm install && npm run dev        # http://localhost:5173 (proxies /api to :8000)
cd ../backend && python -m pytest -q                # needs a database named assistant_test
```
Migrations: `alembic revision --autogenerate -m "msg"` then `alembic upgrade head` (`alembic check` verifies models and migrations agree).

## First-time setup in the dashboard
1. **AI settings > Providers**: add an API key, enable it, press *Test connection*. Leave "Allow personal data" OFF unless the provider has a zero-retention agreement or is self-hosted.
2. **AI settings > Providers > Model prices**: enter real prices and tick *Verified*. They are seeded as $0 on purpose; cost figures are only as good as this table.
3. **AI settings > Model routing**: pick a small model for *answer / extraction / summarization* and a larger one for *answer_large*, plus fallbacks.
4. **AI settings > Retrieval**: set the **embedding provider** (openai or google). The default `hash` embedder is offline and for tests only; it matches poorly on real questions. If you change the embedding model later, re-upload documents and re-save Q&A so all vectors match.
5. **Knowledge base**: upload documents. **Q&A manager**: add greetings, hours, prices; use `{variables}` for values that change.
6. **Channels**: connect Messenger / Instagram / WhatsApp (see Meta setup), press *Test connection*.
7. **Test chat**: try questions, inspect each answer, use *Answer is wrong* to save corrections.

## How a message is answered (token-saving order)
1. **Q&A shortcut** (exact, or similarity above the threshold) -> template with variables filled. No AI call.
2. **Semantic cache** (same context version). No AI call. Only PII-free questions and placeholder-free answers are ever cached, so one customer's answer can never carry another's data.
3. **Retrieval**: top-k chunks, trimmed to a token budget. Nothing relevant -> a canned "I don't know, want a person?" reply. No AI call; the question lands in *Unanswered*.
4. **Minimal prompt**: static system prompt first (prompt-cache friendly), then profile summary, missing-field hint, last N messages, rolling summary, chunks, question. Small model by default; the large model only on a weak match or a complex question.
5. **Output filter**, then reply. Every provider attempt is logged with tokens, cost, latency, and the exact post-filter request.
6. After the reply: profile extraction (every N messages) and rolling summaries, on the cheap model.
Budget: set a monthly limit; at the alert level you are warned, past it replies switch to the cheaper model you choose (or stop, if you enable the hard stop).

## Messaging windows and handover
Messenger/Instagram: free-form within 24 h of the user's last message (up to 7 days for human replies with the HUMAN_AGENT tag, if Meta approved it and you enabled it). WhatsApp: free-form within 24 h, approved templates after.
Blocked sends are stored and flagged; the API answers `409 {needs_template}`. A handover keyword (`agent`, `human`, `এজেন্ট`...) or *Take over* pauses the AI; replying as staff pauses it automatically.

## Profiles, consent, merging
* First message -> anonymous profile. WhatsApp's verified number links to an existing profile with that number automatically.
* A phone/email typed in chat is an **unverified claim**. **Default `merge_policy = suggest`**: a match creates a suggestion that an admin approves on the Profiles page. Setting it to `auto` merges immediately, which means anyone who types another customer's number inherits that profile's summary. Use `auto` only if you accept that.
* Phone/email are AES-GCM encrypted with a separate HMAC column for lookup. Agents see them masked; admins see them in full.
* Consent is asked once, after a real question has been answered (not after a bare "Hi"). Details volunteered earlier are held encrypted and are saved only on "yes", discarded on "no".
* Merges never delete: a snapshot allows **Unmerge**. "delete my data" (EN/BN) and the admin *Delete personal data* wipe the profile tree, facts, merge snapshots and message text.

## Privacy layer
Every LLM request must be a `GuardedRequest`, constructible only by `PrivacyGuard`; provider adapters refuse anything else.
* **Minimization**: only question, profile summary, missing-field names, last N messages, rolling summary and retrieved chunks can be expressed. The summary never contains phone/email.
* **Redaction** (reversible, per request, in memory): phones, emails, cards (Luhn), national IDs, addresses, known names -> `[PHONE_1]`... Replies are restored before sending; invented placeholders are stripped. Names are redacted only in customer text, never in your knowledge content.
* **Per-provider policy**: `allow_pii=false` -> everything redacted. `true` -> contacts/names may pass (names per setting) but cards, IDs and your custom patterns are still redacted. Admin "never send" fields are always omitted.
* **Output filter**: blocks answers quoting the system prompt or containing a phone/email that did not come from the user, your knowledge or your variables.
* **Audit**: AI settings > Budget and cost > *What was sent to the AI providers*.

**Limits:** redaction is regex-based; it reduces leakage but cannot guarantee zero leakage of personal context (unknown names, unusual address formats, details inside ordinary sentences). Extraction necessarily sends the customer's own words. For sensitive deployments use a zero-retention provider or a self-hosted model. WhatsApp's `channel_identities.external_id` is the phone number in plain text because the API needs it.

## Meta setup (verify against Meta's current docs; they change often)
1. developers.facebook.com -> Create app (*Business*). Add *Messenger*, *Instagram* messaging and *WhatsApp*.
2. Webhooks (public HTTPS; locally use ngrok/cloudflared; set `PUBLIC_BASE_URL`): `/webhooks/messenger`, `/webhooks/instagram`, `/webhooks/whatsapp`. Verify token = the value on the Channels page. Subscribe: Page `messages`, `messaging_postbacks`; Instagram `messages`; WhatsApp `messages`.
3. App secret -> `META_APP_SECRET` (signature check). Instagram Login may use a different secret: set it per channel on the Channels page.
4. Tokens/permissions (stored encrypted): Messenger `pages_messaging` (+ `pages_manage_metadata`); Instagram `instagram_basic`, `instagram_manage_messages`; WhatsApp `whatsapp_business_messaging`, `whatsapp_business_management` (account ID = *phone number ID*). Use a System User token for production.
5. In development mode only people with a role on the app can message the assistant.
6. App Review: request Advanced access, complete Business Verification, give a privacy policy URL, a data-deletion URL/instructions ("delete my data" implements the behaviour), use-case text and a screencast. WhatsApp: add a real number and get message templates approved (needed for any send after 24 h).
7. `GRAPH_API_VERSION` (default `v21.0`): bump when Meta retires versions.

## Before going live
* Run a live test on each channel: confirm the Instagram send path (`/me/messages` with a Page token) and the window-error codes (`10/2018278`, `131047`) in `channels/`.
* Set real model prices, an embedding provider, a monthly budget, and review the Privacy tab.
* Put TLS in front of the dashboard (Caddy, Cloudflare Tunnel, ...). Keep `.env` out of version control.
* Streaming responses are **not implemented** (`stream=True` raises); the pipeline uses complete responses.
* Live inbox updates use polling (every few seconds), not websockets.
* Scanned PDFs have no extractable text (OCR is not included); such documents are marked *Failed* with the reason.

## Layout
```
backend/app/   core (config, crypto, auth, retry, rate limit)  models  channels (adapters, signature, windows, Graph client)
               privacy (patterns, redaction, summary, guard)   llm (providers, router/budget/audit)   rag (embeddings, documents, templates)
               services (ingest, identity, merge, consent, pipeline, post_reply, outbound, ...)   api (routers)   worker.py   main.py
backend/alembic  backend/tests (149)  backend/scripts (create_admin, gen_keys, seed)
frontend/src   pages (Dashboard, Inbox, Profiles, ProfileDetail, Knowledge, QAManager, Channels, AISettings, TestChat)  components  lib
```
