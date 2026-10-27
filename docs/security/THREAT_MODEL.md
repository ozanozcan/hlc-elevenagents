# Threat model: hlc-elevenagents

- mode: bootstrap+interview
- date: 2026-10-27
- commit: fbe9e3e

A public, non-runnable excerpt of the ElevenLabs voice integration from HLC, a private multi-tenant playbook SaaS for football coaches (README.md:3-6). The code runs inside HLC's FastAPI backend and Next.js app on the owner's machine, reached by ElevenLabs through an ngrok tunnel (integration write-up line 5); the repo itself is world-readable on GitHub, so its committed content is an attack surface of its own.

## Assets

| asset | why it matters | sensitivity |
|---|---|---|
| ElevenLabs API key (`settings.elevenlabs_api_key`) | Spends the workspace's credits and reads/edits every agent, conversation and secret in it (app/voice/elevenlabs.py:44, :119) | critical |
| Voice-token signing secret (`settings.voice_token_secret`) | Anyone holding it mints a token for any team id and reads or draws in that team's playbook (app/voice/token.py:40-43, :62) | critical |
| Webhook HMAC secret (`settings.elevenlabs_webhook_secret`) | Forges post-call deliveries: inflates or pre-spends a team's monthly minutes (app/api/webhooks/elevenlabs.py:98-104) | high |
| Workspace tool secret (`ELEVENLABS_TOOL_SECRET`, ElevenLabs secret referenced by id) | One of the two credentials on every `/voice-tools/*` call (elevenlabs/README.md:45-47; elevenlabs/tool_configs/search_plays.json:20) | high |
| A team's playbook (saved plays, variants, source calls, SVG drawings) | The tenant data the voice surface exists to reach; cross-team read is the headline breach (README.md:72-74) | high |
| Issued voice token (15-min, team-scoped JWT) in the browser and in ElevenLabs tool headers | Bearer credential for one team's `/voice-tools/*` until expiry (app/voice/token.py:24-25, :50-62; web/app/voice/page.tsx:179) | high |
| HLC dev backend behind the tunnel (stub auth, team admin session) | Anything past the guard is an unauthenticated admin session (app/middleware/tunnel_guard.py:4-6) | critical |
| Per-team voice budget counters in Redis (`voice:secs:*`, `voice:starts:*`, `voice:conv:*`, `voice:webhook:*`) | Billing truth and the cost caps; tampering either denies a team voice or lets spend run past the cap (app/voice/budget.py:3-10, :42-47, :121) | medium |
| ElevenLabs credits and Scribe minutes | Real money per call minute and per audio hour (app/voice/budget.py:100-102; elevenlabs/agent_configs/hlc-playbook.json:50) | medium |
| Coach audio and transcripts held by ElevenLabs | Voice recordings of identifiable people; retention is set in the vendor console, not here | medium |
| Confidentiality of the private HLC codebase | The excerpt and `timeline/*.html` publish HLC branch names, git argv and command-output fragments (timeline/git-timeline.html:229) | medium |
| Public identifiers: agent id, tool ids, test ids, workspace secret id | Harmless only while agent auth stays on and the secret is referenced, not inlined (elevenlabs/agents.json:5; elevenlabs/tools.json:7-31; elevenlabs/README.md:45) | low |
| Owner's reputation / employer trust in a public demo | The repo is a job-application artifact; a published secret or a broken claim lands on the owner | medium |

## Entry points

| entry point | where | who can reach it | reaches assets |
|---|---|---|---|
| Post-call webhook `POST /webhooks/elevenlabs` | app/api/webhooks/elevenlabs.py:96 | anyone (public tunnel host is allow-listed, app/middleware/tunnel_guard.py:58) | Webhook HMAC secret, budget counters, ElevenLabs credits |
| Tunnel guard (every HTTP/websocket request) | app/middleware/tunnel_guard.py:65 | anyone on the tunnel; anyone on the LAN | HLC dev backend, team playbook |
| `/voice-tools/*` server tools (routes hidden; voice token verified by `verify_voice_token`) | app/voice/token.py:65; tool URLs elevenlabs/tool_configs/search_plays.json:7, get_play.json:7, draw_play.json:7 | anyone on the tunnel (path allow-listed, app/middleware/tunnel_guard.py:56); ElevenLabs on the agent's behalf | Team playbook, voice token secret, ElevenLabs credits |
| Session mint `POST /api/v1/voice/session` (route hidden; caller and mint code here) | web/lib/voice.ts:47-51; app/voice/token.py:46; app/voice/elevenlabs.py:41; app/voice/budget.py:69 | signed-in coach (local/LAN only per guard) | ElevenLabs API key (used server-side), voice token, budget counters, credits |
| Scribe voice-note upload (route hidden; outbound call here) | app/voice/elevenlabs.py:111-156 | signed-in coach | ElevenLabs API key, Scribe minutes, budget counters |
| Model-driven client tools `show_play` / `show_draw` (ids come from model output) | web/app/voice/voice-tools.ts:63, :88; web/lib/voice.ts:67-68 | the agent, steered by coach speech or by team data in tool results | Team playbook (read through the coach's own session) |
| Backend SVG injected into the DOM | web/app/voice/voice-ui.tsx:38 | whoever controls play names/calls/notes the hidden renderer embeds | Voice token in page memory, coach session |
| Agent system prompt and tool results (prompt injection via play names, source calls, drawer notes) | elevenlabs/agent_configs/hlc-playbook.json:44 | a team member who can edit plays; anyone who can speak to a live call | ElevenLabs credits, coach trust in answers |
| ElevenLabs agent config push (CLI / `PATCH /v1/convai/tools/{id}`) | elevenlabs/README.md:65-88 | operator with ElevenLabs API key | All agent behaviour, tool URLs, auth headers |
| The public repo contents (code, configs, write-up, screenshots, `timeline/*.html`) | timeline/git-timeline.html:229; docs/demos/elevenlabs/integration-writeup/images/; elevenlabs/README.md:45 | anyone | Private HLC confidentiality, public identifiers, any future committed secret |
| Dev-only page seam `window.__hlcVoiceDev` | web/app/voice/page.tsx:212-227 | script running in the `/voice` page of a non-production build | Client tool handlers, team playbook via the coach's session |

## Trust boundaries

| boundary | from → to | enforced at |
|---|---|---|
| Internet (tunnel) → HLC dev backend | anonymous → stub-auth admin | app/middleware/tunnel_guard.py:71-87 (path allow-list for any non-local Host/X-Forwarded-Host) |
| LAN → HLC dev backend | any LAN device → stub-auth admin | nothing found: private, link-local and `*.local` hosts count as local (app/middleware/tunnel_guard.py:45-52) |
| Internet → webhook state change | anonymous → budget writer | app/api/webhooks/elevenlabs.py:49-76 (HMAC, ±1800 s), :102-104 |
| ElevenLabs tool call → team tenant | ElevenLabs workspace → one team's data | app/voice/token.py:65-104 (HS256, aud/iss/exp, sub=team); workspace-secret check and RLS are in hidden code (README.md:68-74) |
| Webhook payload → team attribution | vendor payload → team id | app/api/webhooks/elevenlabs.py:122-131 (team from mint-time `voice:conv:{id}`, never from payload) |
| Browser → LLM context | coach-held voice token → model | `secret__` dynamic variable convention (app/voice/token.py:3-8; web/app/voice/page.tsx:179); enforcement is ElevenLabs' |
| Model output → browser fetch | LLM-chosen id → same-origin API call | web/app/voice/voice-tools.ts:58-61 (string, ≤64 chars); web/lib/voice.ts:68 (`encodeURIComponent`) |
| Backend renderer output → DOM | server SVG → executable markup | nothing found in this repo: raw `dangerouslySetInnerHTML` (web/app/voice/voice-ui.tsx:38); escaping, if any, is in the hidden renderer |
| HLC backend → ElevenLabs API | API key holder → vendor | app/voice/elevenlabs.py:51-55, :136-141 (key in header, HTTPS, 5 s / 30 s timeouts) |
| Voice token scope | `plays:read` → `plays:draw` | nothing found: scopes parsed (app/voice/token.py:95-102) but enforcement is in hidden routes |
| Developer machine → public GitHub | local files → world-readable | nothing found: no `.gitignore`, no CI, no pre-commit in the repo; the literal-secret test only scans `elevenlabs/` (tests/elevenlabs/test_agent_config.py:38-42, :142) and imports `app.config`, which is absent here |

## Attacker profiles

| attacker | starts with | wants |
|---|---|---|
| Anonymous internet user who finds the tunnel host | The public ngrok host (guessable from scans or a leaked screenshot), HTTP | Reach the stub-auth backend; forge webhook deliveries; flood tool routes to burn credits |
| Coach of another team (signed-in HLC user) | A valid session and their own voice token | Read or draw in another team's playbook by id, or exhaust another team's budget |
| Hostile team member | Edit rights on the team's play names, calls and notes | Stored XSS through rendered SVG; prompt injection into the agent through tool results |
| Device on the same LAN as the dev machine | Network reach to the dev backend on a private IP | Use the stub-auth admin API directly, bypassing every voice control |
| Reader of the public repo | Everything committed, including history and timeline HTML | Secrets, tunnel host, private HLC code fragments, ids to target the ElevenLabs workspace |
| Holder of a leaked ElevenLabs API key | The key | Spend credits, read conversations, repoint tool URLs or headers to exfiltrate voice tokens |
| Malicious npm/PyPI dependency (`@elevenlabs/react`, `httpx`, `PyJWT`, `zod`) | Code execution in the browser or backend | Voice token, session, API key |

## Known controls

| control | covers | where | gaps |
|---|---|---|---|
| Tunnel path allow-list, fails closed for unknown and missing hosts | Internet → backend | app/middleware/tunnel_guard.py:55-87; tests/middleware/test_tunnel_guard.py:60-182 | No-op when `voice_tunnel_host` is unset (:66); LAN and `*.local` hosts are fully trusted (:45-52); relies on ngrok not rewriting Host (elevenlabs/README.md:89-90) |
| Webhook HMAC-SHA256, constant-time compare, ASCII-digit bounded timestamp, ±1800 s window | Webhook entry point | app/api/webhooks/elevenlabs.py:49-76; tests/test_webhooks_elevenlabs.py:125-426 | No request-body size cap seen before `request.body()` (:101); HMAC is computed on every unsigned post |
| Per-conversation dedupe marker (7 days) | Webhook replay / double count | app/voice/budget.py:121-124 | none seen (marker outlives the 30-min signature window) |
| Team from mint-time binding, not payload | Webhook → team attribution | app/api/webhooks/elevenlabs.py:122-131; tests/test_webhooks_elevenlabs.py:100 | Binding write and its TTL are in hidden session code |
| Per-IP webhook rate limit after HMAC, fail closed | Webhook flood | app/api/webhooks/elevenlabs.py:105-111 | Behind the tunnel all callers may share one IP (:105) |
| HS256 voice token: aud, iss, exp, iat, sub required; TTL capped at 900 s; `alg=none` rejected | Tool call → tenant | app/voice/token.py:46-104; tests/voice/test_token.py:33-167 | No revocation or `jti` replay check here; scope check left to callers; token readable by any script in the page |
| Workspace secret header on every server tool, typed `secret_id` form | Tool routes | elevenlabs/tool_configs/*.json (e.g. search_plays.json:19-20); tests/elevenlabs/test_agent_config.py:119-132 | Server-side compare is in hidden code |
| Postgres RLS for tenant isolation | Cross-team read | README.md:72-74 (claim; code hidden) | Not verifiable from this repo |
| Agent authentication on; no client overrides | Agent id alone opens nothing; browser cannot change prompt or ASR | elevenlabs/agent_configs/hlc-playbook.json:64-65; tests/elevenlabs/test_agent_config.py:82, :223 | Static test only; the live agent can drift from the file |
| Budget caps: hourly starts, monthly minutes, fail closed on Redis loss | Credit exhaustion | app/voice/budget.py:69-113; tests/voice/test_budget.py:36-191 | Minutes are credited only after a call ends, so concurrent calls inside one hour can run past the monthly cap; Scribe seconds crediting not in this repo |
| Conversation length cap 600 s | Credit exhaustion per call | elevenlabs/agent_configs/hlc-playbook.json:50 | Vendor-enforced |
| No SVG through the model; ids only, URL-encoded, ≤64 chars | Model output → browser | web/app/voice/voice-tools.ts:1-6, :58-61; web/lib/voice.ts:68 | SVG itself is trusted raw (web/app/voice/voice-ui.tsx:38) |
| Prompt rule: tool results are data, stay on topic | Prompt injection | elevenlabs/agent_configs/hlc-playbook.json:44 | Instruction only, not enforcement |
| API key server-side only; upstream errors never carry vendor body | Key exposure | app/voice/elevenlabs.py:1-7, :27-28, :56-63 | none seen |
| Literal-secret and tunnel-host scan of agent config | Public repo leaks | tests/elevenlabs/test_agent_config.py:38-42, :142-153 | Scans only `elevenlabs/`; cannot run in this excerpt (needs `app.config`); no CI here |
| Dev seam stripped in production builds | `window.__hlcVoiceDev` | web/app/voice/page.tsx:210-213 | Present whenever Next runs in dev mode, which is how the demo runs |

## Out of scope

| item | why |
|---|---|
| Hidden HLC product code (tool routes, session route, play search, drawer, renderer, RLS policies) | Not in this repo; its threat model belongs in the private HLC repo. Rows above that depend on it are marked hidden |
| ElevenLabs platform security (WebRTC transport, `secret__` variable isolation, workspace secret storage) | Vendor-operated; treated as a trusted third party |
| Running this repo as an app | README.md:3-4: it does not start on its own; no build or deploy files exist |
| Production deployment, multi-customer IdP, mTLS | Not built; listed as future changes in README.md:82-87 |
| Mid-call audio injection by a bystander near the coach | Reaches only what the coach's own token already reaches; no privilege change |
| Commit-history mining for past security fixes | Only two commits (2d34a5e, fbe9e3e), neither a fix; HLC history is private |

## Owner answers (interview 2026-10-27)

- Repo visibility: **public** (`gh repo view` 2026-10-27); every commit is world-readable.
- `timeline/git-timeline.html` and `timeline/run.html` (git commands, output and branch names from the private hlc repo) were not checked before they were published: **gap**. They get a scan for secrets and for private paths and names.
- The tunnel guard trusts any LAN, private-IP or `*.local` host (app/middleware/tunnel_guard.py:45-52) in front of a stub-auth dev backend: **gap**; trust loopback only.
- Whether the backend's SVG is escaped before `dangerouslySetInnerHTML` (web/app/voice/voice-ui.tsx:38) depends on the private hlc renderer. The owner is not sure: needs validation against hlc `app/domain/render/svg.py`.

## Open questions

- Is the ngrok tunnel up now, and does its host appear anywhere public? (Owner not sure.)
- Do the private `/voice-tools/*` routes check the workspace secret in constant time, enforce the `plays:draw` scope, and bind the token's conversation id?
- Do Scribe voice notes add their seconds to the monthly counter, or only read it?
- Is there a request-body size limit in front of the webhook (app/api/webhooks/elevenlabs.py:101)?
- What retention and logging do the ElevenLabs settings use for audio and transcripts?
- Is the live ElevenLabs agent still auth-on with no overrides, as the committed config says?
