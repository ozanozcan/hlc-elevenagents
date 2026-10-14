# hlc-elevenagents

**An excerpt, not a runnable app.** This is the ElevenLabs integration code from HLC, a private
multi-tenant playbook SaaS for football coaches. It runs inside that codebase; on its own it does
not start. It is published to show how the integration is built: agent config as code, tenant-safe
server tools, a signed post-call webhook, cost caps, and the voice page.

**Start here:** [the write-up with screenshots](docs/demos/elevenlabs/integration-writeup/hlc-elevenlabs-integration.md) ([PDF](docs/demos/elevenlabs/integration-writeup/hlc-elevenlabs-integration.pdf)) covers what was built, the exact ElevenLabs features used, the live calls and the measured numbers.

Video of the full demo: _to be added_.

## Contents

- [What the demo does](#what-the-demo-does)
- [How it fits together](#how-it-fits-together)
- [What is in this excerpt](#what-is-in-this-excerpt)
- [Design decisions](#design-decisions)
- [What a customer deployment would change](#what-a-customer-deployment-would-change)
- [Tests](#tests)

## What the demo does

A coach opens `/voice`, taps **Start call** and talks to their own playbook:

- "Show our boundary smash on the right hash": the agent finds the saved play and the page draws it.
- "What do we have versus Cover 2?": the agent searches the team's plays and reasons over them.
- "Draw a new play: spread right hash, field mills, boundary smash": HLC's own drawer renders it.

Separately, a ≤60 s voice note on the plays page goes through ElevenLabs Scribe, biased with the
team's own vocabulary, into the call box for the coach to confirm.

## How it fits together

```
 Browser (/voice)                    ElevenLabs agent              HLC backend (hidden here)
 ────────────────                    ────────────────              ─────────────────────────
 Start call ──POST /voice/session──────────────────────────────▶  mints WebRTC token (API key
          ◀── conversation token + 15-min team voice token ──────   stays server-side) + voice JWT
 WebRTC audio ───────────────────▶  Haiku 4.5 turn
                                     server tool call ─────────▶  /voice-tools/*  (voice JWT +
                                       (secret__ header, never        workspace secret; RLS)
                                        shown to the LLM)      ◀── ids + short text, never SVG
 client tool show_play ◀─────────── "show play <id>"
 fetch SVG with the coach's own session ──────────────────────▶  renderer (hidden)
                                     post-call webhook (HMAC) ──▶ minutes per team (budget.py)
```

Boxes marked hidden are HLC product code: the tool routes, the play search and draw handlers, the
keyterm builder and the renderer.

## What is in this excerpt

| Path | What it is |
|---|---|
| `elevenlabs/` | Agent, five tools and four agent tests as code (CLI layout); README with the live-tested header forms |
| `app/voice/token.py` | HS256 voice token: team, scopes, 15-minute expiry |
| `app/voice/elevenlabs.py` | ElevenLabs client: conversation token, Scribe transcription |
| `app/voice/budget.py` | Session starts per hour, minutes per month, fail-closed |
| `app/api/webhooks/elevenlabs.py` | Post-call webhook: HMAC, ±30 min window, dedupe, team from the minted conversation |
| `app/middleware/tunnel_guard.py` | Only two path prefixes reachable through a public tunnel; fails closed for unknown hosts |
| `web/app/voice/`, `web/lib/voice*.ts` | The voice page: call control, tool strip, live transcript, play rendering |
| `tests/…` | Tests for the files above |
| `timeline/git-timeline.html` | How it was built: every git operation the three ElevenLabs chats ran in HLC, on the repo graph (static snapshot, opens from disk) |
| `timeline/run.html` | The build run: 4 waves, 6 agent lanes, time, tokens, commits and board rows (static snapshot) |

## Design decisions

1. **Two credentials on every tool call.** ElevenLabs does not sign server-tool calls, and
   dynamic variables are set by the browser. So each call carries a workspace secret ("our
   ElevenLabs workspace") and an HLC-signed, 15-minute, team-scoped token ("this coach's team")
   passed as a `secret__` variable the LLM never sees.
2. **The token selects the tenant; the database enforces it.** Postgres row-level security does
   the isolation, so a cross-team id looks exactly like a missing one.
3. **No SVG through the model.** Tools return ids and short text; the browser fetches drawings
   with the coach's own session.
4. **Billing truth from the vendor.** Minutes come from the signed post-call webhook, attributed by
   the conversation id stored at mint time, never by a team id in the payload.
5. **Caps fail closed.** Session starts, monthly minutes, draws per conversation and the tool rate
   limit all refuse when Redis is down.
6. **Private agent.** Agent authentication is on, so the agent id alone opens nothing.

## What a customer deployment would change

- The customer's identity provider issues the voice token instead of HLC.
- Auth connections or mTLS instead of a shared workspace secret.
- Per-customer agents, branches or environments instead of one agent.
- A real deployment instead of a local tunnel, with the guard replaced by network policy.

## Tests

Python tests use pytest with ElevenLabs mocked at the HTTP boundary; the web tests run with
`node --test`. The ElevenLabs agent tests in `elevenlabs/tests.json` run with
`elevenlabs agents test <agent_id>`. One limitation: the agent-test grader cannot address a GET
tool's query parameters, so the search test checks the call, not its query.

## License

MIT.
