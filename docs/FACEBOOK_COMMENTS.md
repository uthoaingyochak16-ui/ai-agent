# Post comments (Facebook Pages and Instagram): AI replies, likes and moderation

Comments of every connected Facebook Page and Instagram account arrive in one list (Post comments page), with
filters for platform, Page / account and kind. Instagram works the same way except: the API cannot like an Instagram
comment, and there is no private Messenger reply. Instagram setup: Meta app → Instagram → Webhooks → subscribe
`comments` (callback `<public URL>/webhooks/instagram`); the Instagram account's token needs
`instagram_manage_comments` (and `instagram_basic`). Connect the account on Pages and numbers → Instagram.

The assistant reads comments on the Page's posts, likes them, and replies to questions from the knowledge base
(saved Q&A, programs, documents) with short public replies. Code: `backend/app/services/comments.py`,
`backend/app/channels/page_comments.py`; dashboard: **Post comments**.

## What it does with each comment

| Comment | What happens |
|---|---|
| Question ("kobe hobe?", "fee koto?", "details?", "interested") | like + a short answer from the knowledge base, using the post for context ("kobe?" under a Songhayon post means Songhayon). If the knowledge doesn't have it, a polite "please message the Page" instead of a guess |
| Thanks / prayers / greetings ("Alhamdulillah", "MashaAllah", salam) | like + a short thank-you |
| Only emoji, tags, "nice" | like only (no AI call) |
| Complaint (their own problem) | a polite apology asking them to message the Page, no like; marked **Needs staff** |
| **Against Quantum Method / Quantum Foundation** (cult/fraud/business claims, insults to the founder, propaganda, "stay away") | no reply; staff are told at once (alert webhook + **Needs staff**); hidden or deleted too if you choose so |
| Spam / abuse (ads, loans, links, scams, insults) | no reply; ignored, hidden (default) or deleted, as you choose |
| Reply inside a thread | answered only when the person who started the thread replies to our reply (at most 2 times); conversations between other people are left alone |

Every comment gets a one-line **Why** (the words that made the AI decide) and how sure it is. The AI never hides or
deletes on its own below 80% sure: such comments wait under **Needs staff** with its suggestion. Hidden comments can be
shown again (**Show again**); deleted ones cannot, so hide is the safer choice. A polite question about a controversy
is treated as a question, not as an attack.

Tools on the Post comments page: **Check a comment** (paste any comment: real, spam or against, and why; nothing is
stored or posted) and **Scan recent posts** (checks the comments already on the last 10 posts; spam and attacks are
handled as set, replies to them are only drafted, never posted or liked on their own).

Public replies never mention personal details. The Page's own comments are skipped, so it never answers itself.

**Reactions:** through the API, a Page can only **like** a comment. Love / care / haha are not available to apps.

## Modes (Post comments page, admin)

- **Off** (default): comments are not read.
- **Review**: the AI writes the reply; staff post, edit or dismiss it on the Post comments page. A like on its own still happens.
- **Auto**: the AI posts replies and likes by itself. Above 60 replies an hour, replies wait as drafts (Meta limits reply bursts).

Start with **Review** for a few days, read the drafts, then switch to Auto.
Optional: "Also message the commenter in Messenger" sends one private message after answering a question, so the
conversation goes on in Messenger (the normal chat assistant takes over there).

## Meta setup (one time)

1. **Meta app → Webhooks → Page**: subscribe the `feed` field (next to `messages`), same callback URL
   (`<public URL>/webhooks/messenger`).
2. **Page access token permissions**: `pages_read_engagement`, `pages_read_user_content`, `pages_manage_engagement`,
   `pages_manage_metadata` (hide and delete also use `pages_manage_engagement`) (plus `pages_messaging`, already there for Messenger). Without App Review these work only for
   people with a role on the app; for the public, the app needs App Review for these permissions.
3. **Channels page → Messenger → Test connection**: subscribes the Page to `messages` and `feed`. If the token lacks
   the comment permissions, Messenger stays subscribed and the note says what is missing.
4. **Post comments page**: choose **Review**.

Settings (AI settings API): `comment_mode`, `comment_auto_like`, `comment_reply_to_praise`,
`comment_spam_action` (ignore | hide | delete), `comment_against_action` (notify | hide | delete),
`comment_remove_min_confidence` (0.8),
`comment_private_reply`, `comment_reply_delay_seconds` (20), `comment_max_replies_per_hour` (60),
`comment_thread_max_replies` (2), `comment_max_chars` (300), and the texts `comment_no_answer`, `comment_thanks_reply`,
`comment_complaint_reply`, `comment_private_reply_text` (each with an `_en` twin).

The comment classifier uses the **router** AI route (cheap model); answers use the normal **answer** route.
