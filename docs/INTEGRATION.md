# Connecting the assistant to the main application (Django)

The assistant runs as its own service (its own database, its own Docker stack). The main application shows the
assistant's dashboard inside one of its pages, and its users are signed in to the assistant with their main-app login
(single sign-on). Guests (Messenger / Instagram / WhatsApp people) stay in the assistant's own database; nothing about
them is shared with the main application.

```
 Browser ── main app (Django) page "Assistant"
              └─ <iframe src="https://ASSISTANT/sso#token=…&next=/inbox">
                    assistant dashboard  ──>  assistant API  ──>  assistant database
```

## 1. Shared secret (once)

Generate one secret and put the same value in both applications.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Assistant** `.env`:

```
SSO_SHARED_SECRET=<the secret>
SSO_ISSUER=main-app
FRAME_ANCESTORS=https://app.example.org        # the main app's origin(s), space-separated
```

Then restart the assistant: `docker compose up -d backend frontend`.

**Main app** `settings.py`:

```python
ASSISTANT_URL = "https://assistant.example.org"     # the assistant's public address
ASSISTANT_SSO_SECRET = env("ASSISTANT_SSO_SECRET")  # the same secret, never in source control
```

## 2. The sign-in link (Django)

`pip install pyjwt`

```python
# assistant_link.py
import time
import uuid

import jwt
from django.conf import settings


def assistant_url(user, next_path: str = "/") -> str:
    """A one-time sign-in link to the assistant for this main-app user (valid for 2 minutes, usable once)."""
    now = int(time.time())
    token = jwt.encode({
        "iss": "main-app",                          # must match SSO_ISSUER
        "sub": str(user.pk),
        "email": user.email,                        # the assistant identifies the user by email
        "name": user.get_full_name() or user.email,
        "role": "admin" if user.is_superuser else "agent",
        "iat": now,
        "exp": now + 120,
        "jti": uuid.uuid4().hex,                    # one use only
    }, settings.ASSISTANT_SSO_SECRET, algorithm="HS256")
    return f"{settings.ASSISTANT_URL}/sso#token={token}&next={next_path}"
```

```python
# views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .assistant_link import assistant_url


@login_required
def assistant(request, page="inbox"):
    return render(request, "assistant.html", {"assistant_src": assistant_url(request.user, f"/{page}")})
```

```html
<!-- templates/assistant.html (inside your base layout) -->
<iframe src="{{ assistant_src }}" style="width:100%; height:calc(100vh - 64px); border:0"
        title="Assistant"></iframe>
```

Pages that `next` can open: `/` (overview), `/inbox`, `/profiles`, `/knowledge`, `/qa`, `/test-chat`, `/evaluation`, and
for admins `/channels` and `/ai`.

## 3. Roles

| Token `role` | In the assistant |
|---|---|
| `admin` | everything, including Channels and AI settings |
| `agent` (or none) | inbox, profiles, knowledge, Q&A, test chat |

Name and role are updated from the main app at every sign-in, so the main app is in charge of them. To keep someone
out, deactivate them in the main app (no new link) — or in the assistant, which then refuses their sign-in even with a
valid token.

## 4. Rules the assistant enforces

- Token signed with HS256 and `SSO_SHARED_SECRET`; `iss` must equal `SSO_ISSUER`.
- `exp`, `iat`, `jti` and `email` are required; a token older than 5 minutes (`SSO_MAX_AGE_S`) is refused.
- Each token works once (`jti` is remembered).
- The token travels in the `#fragment`, so it never reaches a server log, and the dashboard removes it from the
  address bar at once.
- The dashboard can only be framed by the origins in `FRAME_ANCESTORS` (plus itself).
- The email/password form keeps working for admins created in the assistant (emergency access).

## 5. Checklist

1. Same secret in both `.env` / settings.
2. `FRAME_ANCESTORS` = the main app's exact origin (`https://…`, no path, no trailing slash).
3. Both sites on HTTPS (browsers block a secure page framing an insecure one).
4. Open the main app's "Assistant" page: the dashboard appears signed in as that user.

When this sign-in fails, the dashboard shows the reason (expired, already used, wrong secret or issuer, user
deactivated). The assistant's API is documented by the backend itself at `/docs` (inside the stack:
`docker compose exec backend python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/openapi.json').read().decode())"`;
it is not exposed publicly).
