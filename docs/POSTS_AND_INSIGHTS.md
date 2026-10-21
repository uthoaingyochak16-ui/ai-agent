# Social media: posts, insights, audience, ads, WhatsApp

Page: **Social media** (code: `backend/app/services/posts.py`, `backend/app/channels/page_posts.py`).

- **Create a post**: pick one or more Facebook Pages / Instagram accounts, write the text (or **Write with AI**: it uses
  the chosen program's real details and what worked before), add an image (upload or a link) and a link (Facebook),
  then **Save draft**, **Schedule** (your local time) or **Publish now**. Scheduled posts are published by the worker
  within a minute of their time. Instagram posts need an image.
- **Posts**: every post (made here or on Facebook / Instagram directly), with engagement (reactions + comments +
  shares + saves), reach, engagement rate, and its topic (labelled by the AI). **Refresh numbers** reads the latest 50
  posts of each account; the worker also does it every 3 hours.
- **Insights**: when posts do best (weekday x hour heat map, Bangladesh time), which topics and formats do best (and
  how much better than the average), the best and weakest posts, and **The AI's plan**: what to post in the next two
  weeks, when, in which format and why, including the upcoming programs. The AI never posts by itself.

- **Calendar**: the month with every post (published, scheduled, waiting for approval, AI drafts at their suggested
  time) and the programs' days. Drag a post that is not published yet to another day to move it.
- **Approval and history**: with "Staff posts need an admin's approval" on, a non-admin's scheduled / published post
  waits as *pending approval*; an admin approves or sends it back. Each post keeps who did what (History).
- **A/B tests**: "add version B" in Create; version B goes out a day later at the same time; Post insights shows the
  winner (engagement rate, then engagement).
- **Drafts the AI prepares** (never published by themselves): *Program drafts* (an invitation + a day-before reminder
  for each upcoming program; nightly when on) and *AI: plan 2 weeks* (the AI's plan as drafts at the suggested times;
  weekly when on). Write with AI also makes Reel scripts and Story frames.
- **Audience & trends**: followers and reach per account, saved daily (trend), Instagram online hours and age / city /
  gender; the mood of comments week by week (an alert fires when negative comments jump); what guests ask most (AI
  groups chats, comments and unanswered questions, with a post idea each); per program: posts, engagement, people who
  asked, form links given, leads; weekly AI report (Saturdays 09:00, also to the alert webhook).
- **Ads**: the AI prepares ad drafts (objective, audience, budget, days, text) from a goal and a program; a person
  approves and starts them in Ads Manager. Nothing spends money here. Connect an ad account id + a token with
  `ads_read` to see the last 30 days of campaign results (read-only).
- **WhatsApp broadcast** (admin): an approved template to every guest who wrote on WhatsApp and gave consent (optionally
  only those active in the last N days); about 4 messages a second; can be cancelled; replies arrive in the Inbox.

## Meta permissions

| What | Facebook Page token | Instagram |
|---|---|---|
| Publish | `pages_manage_posts` | `instagram_content_publish` |
| Read posts and numbers | `pages_read_engagement`, `read_insights` | `instagram_basic`, `instagram_manage_insights` |

Outside the app's own testers, these need Meta App Review. Meta retires some insight metrics now and then; a metric it
refuses is simply left out (reactions, comments and shares always come with the post).

**Uploaded images** are served from `<PUBLIC_BASE_URL>/api/media/...`, because Facebook and Instagram fetch the image
from a public link. `PUBLIC_BASE_URL` in `.env` must be the current public address (the quick tunnel's address changes on
every restart: update it, or paste an image link instead).

Audience numbers: `read_insights` (Page), `instagram_manage_insights` (Instagram). Ad results: `ads_read` on the ad
account. WhatsApp broadcast: the number's token (`whatsapp_business_messaging`) and approved templates.

Creating ads automatically is deliberately not built: it would spend money; drafts + Ads Manager keep a person in charge.
