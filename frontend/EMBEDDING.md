# One navigation bar only: running the assistant inside the main application

The assistant has **no navigation bar of its own** any more (no side bar, no top bar, no second menu column inside a page):

| Where | How you move around |
|---|---|
| Home (Overview) | Every page is a big button, in three groups (`components/Launcher.tsx`, data in `src/nav.ts`) |
| Every other page | A small "Assistant home" link at the top, and a round **Menu** button in the corner that opens the same buttons in a panel |
| Knowledge | The kinds of knowledge are a row of buttons above the content (was a 250 px side menu) |
| Inbox | The folders are a row of buttons above the conversations (was a 220 px side menu) |

So it can sit inside the main application, which keeps the only navigation bar.

## Build settings (all optional)

| Variable | Example | Effect |
|---|---|---|
| `VITE_EMBEDDED` | `true` | Also hides the assistant's own theme and sign-out buttons on the home page (the main application has them) |
| `VITE_BASE_PATH` | `/assistant` | The assistant is served under this URL prefix (router basename and asset paths) |

```bash
VITE_EMBEDDED=true VITE_BASE_PATH=/assistant npm run build
```

## In the main application

One link is enough, for example "AI Assistant" → `/assistant`. The assistant's own home buttons and Menu take it from there.
If you prefer, the main bar can also list the pages directly: the paths are in `src/nav.ts` (`/assistant/inbox`, `/assistant/train`,
`/assistant/knowledge`, `/assistant/channels` and `/assistant/ai` for administrators).

## Sign-in

The main application opens `/assistant/sso#token=<one-time token>&next=/inbox` (see docs/INTEGRATION.md), so the person never sees the
assistant's own login page.

## Things to check after embedding

- The round Menu button is fixed to the bottom-right corner (`z-50`); move it in `components/Launcher.tsx` if the main application has something there.
- The page is no longer full-height by itself (`min-h-screen` is on the wrapper in `components/Layout.tsx`); remove it if the main application already sizes the page.
