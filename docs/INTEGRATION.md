# The assistant and the main application (Quantum Foundation software)

The assistant is the **Social Inbox** of the main application, run as its own service (own database, own Docker
stack). It talks to guests on Messenger / Instagram / WhatsApp, answers from its knowledge, shares the registration
form of a program a guest wants to join, and puts interested guests on a lead list that flows into the main app as
guests + tasks.

```
 Guest (Messenger) ──> AI assistant ──────────────── Meta webhook, AI, knowledge, inbox, lead list
                         │  ◄─ open programs + form links ── main app Partner API  GET  /api/partner/v1/programs
                         │  ── interested guest (lead) ────> main app Partner API  POST /api/partner/v1/leads
                         │                                     (guest + task, routed to a communicator)
 Staff ──> main app "সোশ্যাল ইনবক্স" ──iframe + one-time sign-in──> assistant dashboard
```

Guests' chats stay in the assistant; only a lead (name, phone, interest, a short note) goes to the main app.

## Settings

**Assistant `.env`**

| Setting | Value |
|---|---|
| `MAIN_APP_API_URL` | the main app's backend (from the assistant's containers), e.g. `http://host.docker.internal:8000` |
| `MAIN_APP_API_KEY` | a Partner API key from the main app with the scopes `events:read` and `guests:write` |
| `MAIN_APP_PUBLIC_URL` | where guests open forms, e.g. `https://crm.quantummethod.org.bd` |
| `SSO_SHARED_SECRET` | a random secret, the same as the main app's `ASSISTANT_SSO_SECRET` |
| `SSO_ISSUER` | `main-app` (the same as the main app's `ASSISTANT_SSO_ISSUER`) |
| `FRAME_ANCESTORS` | the main app's address, e.g. `https://crm.quantummethod.org.bd` |

**Main app `.env`** (see its `ASSISTANT.md`)

| Setting | Value |
|---|---|
| `ASSISTANT_URL` | the assistant dashboard's address, e.g. `https://assistant.example.org` |
| `ASSISTANT_SSO_SECRET` | the same secret as above |
| `ASSISTANT_SSO_ISSUER` | `main-app` |

The Partner API key is made in the main app: Settings -> External Database Access (super admin), scopes
`events:read` + `guests:write`. A secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## What happens

- **Programs.** Every 15 minutes the worker brings in the main app's published events that are not over (and
  removes closed ones). Each becomes a program with its form link `MAIN_APP_PUBLIC_URL/register?q=<id>`, so every
  answer can use it; Leads -> "Sync programs now" does it at once.
- **Leads.** When a guest shows real interest in attending or in knowing more, the AI records a lead (tool
  `create_lead`), shares the program's form link, and asks once for a phone number if none is known. With a phone the
  lead goes to the main app at once (a guest + a task in New Leads, assigned by its lead routing); without one, or
  while the main app cannot be reached, it waits and the worker tries again every 5 minutes. Leads page: the list,
  its status, "Send now".
- **Social Inbox.** The main app's page `/admin/social` asks its backend for a one-time sign-in link
  (`GET /api/social/assistant-link`, 2 minutes, single use) and shows the assistant dashboard in an iframe. Social
  Inbox managers (`social.manage`) are admins in the assistant, everyone else with `social.view` an agent.

## Security

- Sign-in tokens: HS256 with the shared secret, issuer checked, at most 5 minutes old, each usable once; the token
  is in the `#fragment` (never in a server log) and removed from the address bar at once.
- The dashboard can be framed only by `FRAME_ANCESTORS`; the main app allows only `ASSISTANT_URL` in its frame-src.
- The Partner API key is stored only in the assistant's `.env`; the main app keeps only its hash.
- Both sites must be on HTTPS in production (a secure page cannot frame an insecure one).

## Testing the two together on one computer

The main app runs from its folder with `.env.assistant-test` (development mode, http://localhost:8080) and talks to
the assistant's **staging** copy (http://localhost:3001); the live assistant is not touched.

```bash
# main app (its folder)
docker compose --env-file .env.assistant-test -f docker-compose.db.yml up -d
docker compose --env-file .env.assistant-test -f docker-compose.yml up -d --build
# assistant staging (this folder), with the MAIN_APP_* / SSO_* / FRAME_ANCESTORS values in a compose override
docker compose -f docker-compose.staging.yml -f staging-mainapp.override.yml up -d
```

Then open http://localhost:8080, sign in, and open সোশ্যাল ইনবক্স.
