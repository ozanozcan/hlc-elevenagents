# ElevenLabs in HLC: a voice agent and Scribe

**What it is:** a write-up of how I integrated ElevenLabs into High Level Coaching (HLC), my multi-tenant playbook SaaS for football coaches. It has two parts. An ElevenAgents agent lets a coach talk to their own playbook. Scribe turns a sideline voice note into a play call. The screenshots come from the live rehearsal calls on 2026-10-13 and from the ElevenLabs dashboard.

**What it isn't:** a hosted product or a tutorial app. HLC runs locally on development auth and is not deployed. The agent reaches it through an ngrok tunnel that exposes two path prefixes and nothing else. The code in this repo is an excerpt: the HLC product code it calls, such as the play search, the drawer and the renderer, stays private.

**Why it exists:** I wanted to use ElevenLabs the way an enterprise customer would, inside a codebase that already has tenancy, auth, budgets and tests, and to write down what that took. A PDF copy of this page is in [`hlc-elevenlabs-integration.pdf`](hlc-elevenlabs-integration.pdf).

## Contents

- [What a coach sees](#what-a-coach-sees) — two live calls, and the call flow step by step
- [ElevenLabs products and features used](#elevenlabs-products-and-features-used) — the exact list, by product name and config key
- [How a tool call stays inside one team](#how-a-tool-call-stays-inside-one-team) — two credentials, RLS, and why the SVG never reaches the LLM
- [Cost controls and the signed webhook](#cost-controls-and-the-signed-webhook)
- [Sideline voice notes with Scribe](#sideline-voice-notes-with-scribe)
- [The agent as code, and its tests](#the-agent-as-code-and-its-tests) — config in the repo, 4/4 agent tests, a drift-checking preflight
- [Measured numbers](#measured-numbers)
- [What went wrong on the way](#what-went-wrong-on-the-way) — platform behaviour I only found in live calls
- [What is real and what is not](#what-is-real-and-what-is-not)
- [Sources](#sources)

---

## What a coach sees

The coach opens `/voice`, starts a call and asks for a play out loud. The agent finds it in the team's saved plays and the drawing appears on screen. Both screenshots below are real calls from the rehearsal on 2026-10-13.

![Live call: the coach asks for a hammer concept, the agent lists the two matching saved plays, the coach picks one and its diagram is drawn below the transcript](images/live-call-hammer.png)

*Live call. "With Hammer Concept" matched two saved plays, so the agent asked which one, then showed Spread Boundary Hammer Field Dust.*

The second call is less tidy, and I've kept it because it shows an early agent version's limit. The coach asked to draw a new play, and the agent said it could only show saved ones. That call ran agent v1, before the `draw_play` tool existed. The coach then asked for a field mills concept and got the saved play.

![Live call on agent v1: the agent declines to draw a new play, then finds and shows Spread Boundary Smash Field Mills](images/live-call-field-mills.png)

*Live call on agent v1, before `draw_play` was added: the agent declines to draw, then shows a saved field mills play.*

The four phone-width screens below show a full call step by step: search, show, draw, show the new drawing. The tool strip names each tool as it runs, marked `SERVER` for a call into HLC and `CLIENT` for a call handled in the browser. These four are captures from automated UI tests (Playwright at 390 px with a stubbed conversation), not from a live call. I've used them because a live call doesn't stop between states.

![Four phone screens: search_plays running, a saved play shown, draw_play running, and the new drawing shown with the agent saying "Done. Smash to the boundary, Mills to the field"](images/voice-page-states.png)

*UI test captures at 390 px, conversation stubbed: one find, one draw.*

---

## ElevenLabs products and features used

The list below is taken from the agent config in [`elevenlabs/`](../../../../elevenlabs/) and from the code that calls the API. It covers only what HLC actually uses.

| Product | How HLC uses it |
|---|---|
| **ElevenAgents** (Agents Platform, API path `convai`) | One agent, "HLC Playbook", on `claude-haiku-4-5` at temperature 0 |
| **Speech to Text, Scribe v2** | `POST /v1/speech-to-text`, model `scribe_v2`, batch, for voice notes |
| **Scribe Realtime** | The agent's own speech recognition: `asr.provider: scribe_realtime`, `quality: high`, `pcm_16000` |
| **`@elevenlabs/react` 1.17.0** | `useConversation` and `startSession({ connectionType: "webrtc" })` |
| **ElevenLabs CLI** (`@elevenlabs/cli`) | The project layout, and `elevenlabs agents test` |

| Agent feature | Setting |
|---|---|
| Server tools (webhook tools) | `search_plays` (GET), `get_play` (GET), `draw_play` (POST), each with an `api_schema` and a `response_timeout_secs` |
| Client tools | `show_play`, `show_draw`, with `expects_response: true`, handled by `clientTools` in the React SDK |
| Dynamic variables | Carry the per-session HLC voice token into tool headers, using the typed `{"variable_name": ...}` header form |
| Workspace secrets | A shared-secret tool header, referenced by `secret_id` |
| ASR keywords | `asr.keywords`: 19 football terms such as trips, bunch, smash and mesh |
| Turn-taking | `turn.mode: turn`, `turn_timeout: 7` |
| Conversation limit | `max_duration_seconds: 600` |
| Client events | `audio`, `interruption`, `agent_response`, `user_transcript` and others, which drive the live transcript |
| Agent authentication | `platform_settings.auth.enable_auth: true`: the agent ID alone opens nothing |
| Agent testing | 2 tool-call tests and 2 LLM next-reply tests, attached through `platform_settings.testing` |
| Conversation token | `GET /v1/convai/conversation/token`, minted on the server so the API key never reaches the browser |
| Post-call webhook | `post_call_transcription`, HMAC-signed through the `ElevenLabs-Signature` header |
| Scribe keyterms | `keyterms`, built from the team's own play and formation names |

The voice is set in the dashboard, not in the repo config: the agent speaks as "Eric – Smooth, Trustworthy" on the V4 Turbo model family.

![ElevenLabs dashboard for the HLC Playbook agent: the system prompt, the first message, Claude Haiku 4.5 as the LLM, and the voice panel](images/elevenlabs-agent-dashboard.png)

*The agent in the ElevenLabs dashboard. The prompt, first message and LLM shown here are synced from the repo.*

---

## How a tool call stays inside one team

ElevenLabs signs post-call webhooks but does not sign server-tool calls. Dynamic variables also come from the browser. So a tool call that only proved "this came from our ElevenLabs workspace" would let any caller pick any team. Every call to `/voice-tools/*` therefore has to carry two credentials, and HLC rejects it unless both check out:

| Credential | Proves | Where it comes from |
|---|---|---|
| Workspace secret | The call came from our ElevenLabs workspace | A workspace secret referenced by `secret_id` in the tool's headers |
| HLC voice token | The call is for this coach's team | An HS256 JWT that HLC mints per session: one team, named scopes, 15-minute expiry (`app/voice/token.py`) |

The voice token travels as a `secret__` dynamic variable, so the LLM never sees it. The token only *selects* the team. Postgres row-level security still *enforces* it, so another team's play ID looks exactly like a missing one: both return 404.

```
Browser (/voice)            ElevenLabs agent          HLC backend
────────────────            ────────────────          ───────────
Start call ── POST /voice/session ──────────────────▶ mint WebRTC token
           ◀── conversation token + team voice token   + 15-min voice JWT
WebRTC audio ─────────────▶ Haiku 4.5 turn            (API key stays here)
                            server tool call ───────▶ /voice-tools/*
                                                       secret + JWT, RLS
                                             ◀─────── ids + short text,
                                                       never SVG
show_play (client tool) ◀── "show play <id>"
fetch SVG with the coach's own session ─────────────▶ renderer
                            post-call webhook ──────▶ minutes per team
                            (HMAC-signed)
```

Drawings never pass through the model. A server tool returns an ID and a short description. The client tool tells the browser to fetch the SVG with the coach's own session. That keeps every turn short and cheap, and the LLM can't change a drawing. The drawer's own notes are filtered and paraphrased by the agent, never read out word for word.

The tunnel guard (`app/middleware/tunnel_guard.py`) lets exactly two path prefixes through the public tunnel, the tool routes and the webhook, and fails closed for any unknown host. The voice page and the session endpoint are never reachable from outside.

---

## Cost controls and the signed webhook

Each cap is checked before any call to ElevenLabs, and each one fails closed if Redis is down.

| Cap | Default | Where |
|---|---|---|
| Session starts per team | 30 per hour | `app/voice/budget.py` |
| Voice minutes per team | 120 per month, counting agent calls and Scribe audio | `app/voice/budget.py` |
| Conversation length | 600 s | Agent config, `max_duration_seconds` |
| Draws per conversation | 10 per voice token | Draw stash |
| LLM spend on draws | The same per-team bucket as a typed draw, so voice can't bypass the budget | The drawer path |

![Phone screens for three failure states: monthly minutes used up, microphone blocked, and voice not configured on the server](images/voice-page-failure-states.png)

*UI test captures at 390 px: what the coach sees when a cap or a precondition stops the call.*

Minutes come from ElevenLabs' post-call webhook, not from the browser. The webhook handler (`app/api/webhooks/elevenlabs.py`) checks the `ElevenLabs-Signature` header (`t=<unix seconds>,v0=<hex>`) with a constant-time compare and rejects timestamps more than 30 minutes off in either direction. It drops duplicate deliveries, and it attributes minutes by the `conversation_id` that HLC stored when it minted the session, never by a team ID in the payload. In the rehearsal it recorded 354 s for three calls.

![ElevenLabs "Create a Webhook" dialog with the HLC callback URL and HMAC as the auth method](images/elevenlabs-webhook-hmac.png)

*Setting up the post-call webhook with HMAC. I've hidden the tunnel hostname.*

---

## Sideline voice notes with Scribe

On the plays page a coach can record a note of up to 60 seconds. HLC sends it to Scribe v2 with `keyterms` built from that team's own formation and concept names, so "trips right smash" comes back spelled the way the team spells it. Spoken numbers that form a team term are rewritten: "eighty-eight" becomes "88" and "five out" becomes "5-out". Other numbers are left alone. The text lands in the call box, and nothing is drawn until the coach edits it and presses Draw play. Safari on iPhone records `audio/mp4`, so HLC accepts that as well as `audio/webm`.

![Four phone screens of the voice-note flow: idle, recording with a timer, transcribing, and the transcript "trips right smash" in the call box](images/scribe-voice-note.png)

*UI test captures at 390 px with a stubbed transcription: record, transcribe, edit, then draw.*

---

## The agent as code, and its tests

The agent, its five tools and four tests live in [`elevenlabs/`](../../../../elevenlabs/), in the CLI's project layout (`agents.json`, `tools.json`, `tests.json` and the three config folders). I sync changes with the REST API (`PATCH /v1/convai/agents/{id}` and `/v1/convai/tools/{id}`) rather than `elevenlabs tools push`, because CLI 1.4.0 crashed after creating the tools on the first push.

The four agent tests, run with `elevenlabs agents test`:

| Test | Type | Checks that the agent… |
|---|---|---|
| `search_boundary_smash` | Tool call | Calls `search_plays` when asked for a saved play |
| `draw_spread_smash` | Tool call | Calls `draw_play` with the coach's call |
| `unresolved_draw_asks_hash` | Next reply | Asks the coach which hash when the drawer can't resolve the call, without reading the drawer's note word for word |
| `decline_off_topic_weather` | Next reply | Declines a question that has nothing to do with the playbook |

```text
Running 4 test(s) for agent 'HLC Playbook'...
✓ decline_off_topic_weather: passed
✓ draw_spread_smash: passed
✓ search_boundary_smash: passed
✓ unresolved_draw_asks_hash: passed
Total: 4 | Passed: 4 | Failed: 0
```

Before a demo, a preflight script (`scripts/voice_demo_preflight.py`) checks the setup without spending any quota. It confirms that the tunnel blocks the main API, that the tool routes and the webhook refuse unsigned or unauthenticated calls, and that the live agent and tools still match the repo:

```text
PASS tunnel blocks /api/v1 — HTTP 404
PASS tunnel /voice-tools refuses — HTTP 401
PASS tunnel webhook refuses unsigned — HTTP 401
PASS agent drift — 6 fields match the repo
PASS tool drift search_plays — request_headers match the repo
PASS tool drift draw_play — request_headers match the repo
```

*Excerpt; the full run has 13 checks, all passing.*

On the HLC side, ElevenLabs is mocked at the HTTP boundary. There are 314 backend voice tests and 60 web tests. The budget and webhook guards, among others, were checked by mutation: I broke each guard and confirmed that a test failed.

---

## Measured numbers

These come from ElevenLabs' conversation logs and HLC's server logs for the rehearsal on 2026-10-13. They measure server-side tool time, not end-to-end audio latency.

| Measure | p50 | Max | Calls |
|---|---|---|---|
| `draw_play`, from tool call to result | 2.5 s | 4.9 s | 12 |
| `search_plays` | 0.37 s | 0.54 s | 3 |
| `show_play` / `show_draw` (browser fetch of the SVG) | 0.2 s | 0.22 s | 6 |
| Minutes recorded by the post-call webhook | 354 s for three calls (13 + 130 + 211 s) | — | 3 |

A new draw takes about 3 s from the tool call to the drawing on screen. The agent says "Drawing that now" first, which covers the wait, so I left `draw_play` synchronous rather than switching to `execution_mode: async`.

![ElevenLabs conversation history for the HLC Playbook agent: seven calls on agent versions v1 and v3, six evaluated Successful and one Error](images/elevenlabs-conversations.png)

*Conversation history from the rehearsal. The v1 calls came before `draw_play` existed; the v3 calls draw plays.*

The one Error is the last call of the night. I believe it is the call that was cut with code 1002 when the account's quota ran out, but I didn't open its log to confirm.

---

## What went wrong on the way

The first live smoke test found four platform details that the docs and my unit tests didn't. I fixed each one the same day and wrote it into the agent README.

| What happened | Cause | Fix |
|---|---|---|
| The voice token arrived as a literal `{{voice_token}}` string | ElevenLabs does not template `{{var}}` inside a plain-string header value | Use the typed `{"variable_name": ...}` header form; a static test now rejects plain-string headers |
| The shared-secret header was empty | Workspace secrets are resolved by ID, not by name | Reference the secret by `secret_id` |
| `elevenlabs tools push` crashed after creating the tools | A bug in CLI 1.4.0 | Sync tools and the agent with `PATCH` through the REST API |
| Searches for spoken phrases returned nothing | The search matched the whole phrase | Match on individual words |

Two more turned up later. The agent API rejects `reasoning_effort` for Haiku, so the config leaves it out. The agent-test grader can't address GET query parameters, so the search test asserts that the tool was called, not what it searched for.

In the rehearsal itself, the call button hung up when a coach used it as push-to-talk. I fixed its tap handling and added unit tests for it.

---

## What is real and what is not

- **Real:** the ElevenAgents conversations, server tools called over the tunnel into HLC's API, Scribe transcription, HLC's production drawer and renderer, Postgres RLS, the signed webhook, and the 4/4 agent tests against the live agent.
- **Mocked:** ElevenLabs in HLC's own pytest suite, and the conversation in the 390 px UI test captures above.
- **Not production:** HLC runs locally on development auth and is not deployed. The tunnel is a free ngrok domain.
- **Not done yet:** a recorded video of a full call. The quota ran out during the rehearsal.

---

## Sources

- `elevenlabs/agent_configs/hlc-playbook.json`, `elevenlabs/tool_configs/`, `elevenlabs/test_configs/`: the agent, tools and tests
- `app/voice/token.py`: voice token, HS256, 900 s maximum TTL
- `app/voice/elevenlabs.py`: conversation token and Scribe calls
- `app/voice/budget.py` and `app/config.py`: cap defaults (30 starts per hour, 120 minutes per month)
- `app/api/webhooks/elevenlabs.py`: HMAC check, ±1800 s window
- `app/middleware/tunnel_guard.py`: the two-prefix allow-list
- `web/app/voice/page.tsx`: `useConversation`, `startSession({ connectionType: "webrtc" })`, client tools
- Agent test run and preflight output: saved from the 2026-10-13 rehearsal
- Latency figures: ElevenLabs conversation logs and HLC server logs, 2026-10-13
- Screenshots: live calls and the ElevenLabs dashboard, 2026-10-13 and 2026-10-14; phone-width captures from the Playwright UI tests
