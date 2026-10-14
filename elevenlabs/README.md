# ElevenLabs agent config (HLC voice demo)

The HLC voice agent, defined as code for the ElevenLabs CLI (`@elevenlabs/cli` v1.4.0).
Nothing in this folder is pushed by a build lane. Pushing is the operator's gate step.

`tests/elevenlabs/test_agent_config.py` checks this folder in normal CI, with no ElevenLabs
access: every JSON file parses, the agent only references registered tools, every server tool
carries both auth headers in the typed form, and no literal secret or tunnel host appears.

## Contents

- `agents.json`: the agent registry (the shape the CLI wrote). It points at
  `agent_configs/hlc-playbook.json`.
- `agent_configs/hlc-playbook.json`:
  - LLM `claude-haiku-4-5` (d#1114), chosen for turn latency. No `reasoning_effort`: ElevenLabs
    rejects the agent update with 400 "Reasoning effort is not supported for this LLM" for this model.
  - The system prompt and the first message "Which play do you want to see — or should I draw a
    new one?".
  - `asr.keywords`: a static list of generic football terms. Team-specific keywords per session
    are **not** in this run. They would need the `asr.keywords` client override enabled, and an
    override that is not enabled is silently ignored
    (https://elevenlabs.io/docs/eleven-agents/customization/overrides). No overrides are enabled.
  - `conversation.max_duration_seconds: 600`, set by board decision #1118. JSON has no comments,
    so the reason lives here.
  - `turn`: the CLI's default turn settings, written out so they are reviewable.
  - `platform_settings.testing.attached_tests`: the attached test suite (see "Tests" below).
- `tools.json`: the tool registry, five tools.
- `tool_configs/`:
  - `search_plays.json` (**webhook**): `GET https://{{system__env_hlc_tunnel_host}}/voice-tools/plays?q=…`.
  - `get_play.json` (**webhook**): `GET …/voice-tools/plays/{play_id}`.
  - `draw_play.json` (**webhook**): `POST …/voice-tools/draw` with body `{"call": "…"}`.
    `response_timeout_secs` is 40, because the drawer has a 35 s server deadline.
  - `show_play.json` (**client**): the `/voice` page fetches `GET /api/v1/plays/saved/{play_id}`
    with the coach's own session and renders the backend SVG. Optional `hash` (`left`/`right`).
  - `show_draw.json` (**client**): the page fetches `GET /api/v1/voice/draws/{draw_id}` and
    renders its SVG. No SVG passes through the model (d#1116).
- `tests.json` and `test_configs/`: the agent test suite (see "Tests" below).

Every webhook tool sends the same two headers (d#1112):

- `Authorization` as `{"variable_name": "secret__hlc_voice_token"}`: the HLC voice token. A plain
  string header is sent literally (and 401s), so the typed form is required. It takes the
  variable's whole value: the browser passes `Bearer <token>` as a `secret__` dynamic variable,
  so it is never shown to the LLM.
- `X-HLC-Tool-Secret` as `{"secret_id": "DcBy7Zhf0rpUTiuRCd1N"}`: a reference to the workspace
  secret `hlc_tool_secret`, by its **id**. Its value equals `ELEVENLABS_TOOL_SECRET` in HLC's
  `.env` and is never stored here.

## Tests

Four tests, written in the CLI's `tests add --template basic-llm|tool` shapes:

| Test | Type | Checks |
|---|---|---|
| `search_boundary_smash` | tool call | "show our boundary smash" → `search_plays` with `q` matching `(?i)smash` |
| `draw_spread_smash` | tool call | "draw spread right hash, field mills, boundary smash" → `draw_play` with `call` matching `(?i)spread.*smash` |
| `decline_off_topic_weather` | next reply | "what's the weather tomorrow?" → declines, steers back to the playbook, calls no tool |
| `unresolved_draw_asks_hash` | next reply | after a canned `draw_play` result `{"status": "unresolved", "notes": ["Which hash is the ball on?"]}` → asks which hash, as one question |

The phrasings use the demo team's own vocabulary (`demos/default-team/snapshot.json`, d#1117).
The `draw_play` result in the last test is canned in `chat_history`, so no test needs the tunnel.
Each test passes a placeholder `secret__hlc_voice_token` (`Bearer unit-test-placeholder`). It is
not a token: if a real tool call ever went out with it, HLC would answer 401.

## Operator setup before the first push

1. **Workspace environment variable `hlc_tunnel_host`.** Set it to the bare tunnel host, with
   no scheme and no path (for example `<your-static-domain>` from ngrok). Tool URLs accept
   `{{system__env_<label>}}` as long as the URL starts with a literal `https://`
   (https://elevenlabs.io/docs/eleven-agents/integrate/environment-variables).
   Set HLC's `VOICE_TUNNEL_HOST` to the same value. The tunnel guard then serves only
   `/voice-tools/*` and `/webhooks/elevenlabs` on that host.
2. **Workspace secret `hlc_tool_secret`.** This exists (created 2026-10-08). Tool configs reference it
   by its **id**, `DcBy7Zhf0rpUTiuRCd1N` (`GET /v1/convai/secrets` lists ids); the name is accepted
   at push time but never resolves at call time.
3. **Tool ids.** `search_plays` and `show_play` exist; their ids are in `tools.json`. CLI 1.4.0
   `tools push` created them, then crashed before writing ids back, so a re-push makes duplicates.
   Change an existing tool with `PATCH /v1/convai/tools/{id}` (body `{"tool_config": <the
   tool_configs file>}`).
4. **New tools** (`get_play`, `draw_play`, `show_draw`) have no id yet. After creating them, write
   each id into `tools.json`. The static test then fails until you also:
   - add the id to `conversation_config.agent.prompt.tool_ids` in the agent config, and
   - replace the `"id": "draw_play"` placeholder in `test_configs/draw_spread_smash.json`
     (`tool_call_parameters.referenced_tool`) with `draw_play`'s real id.
5. **Tests.** `elevenlabs tests push` creates the tests and writes their ids into `tests.json`.
   The static test then fails until each id is listed as `{"test_id": "<id>"}` in
   `platform_settings.testing.attached_tests`. Push the agent again, then run
   `elevenlabs agents test <agent_id>`.
6. **ngrok.** Do not rewrite the Host header (`--host-header`). The guard keys on the
   tunnel host. It also checks `X-Forwarded-Host` as a fallback.

## Unverified at build time

- The registry entry keys (`name` / `config` / `id`) in `tools.json` and `tests.json` follow the
  CLI's legacy layout; only `agents.json` was written by the CLI itself. If `push` complains, run
  `elevenlabs tools pull` / `elevenlabs tests pull`, then reconcile the entries.
- The test config fields were checked against the request schemas embedded in CLI 1.4.0
  (`CreateToolCallUnitTestRequest`, `CreateResponseUnitTestRequest`). Those schemas have no
  per-test tool-mocking field, so the canned `draw_play` result lives in `chat_history`. Whether a
  tool-call test executes the real tool, and whether test runs spend credits, was not verified.

## Agent test limitation (2026-10-13)

`search_boundary_smash` asserts that `search_plays` is called but does not check `q`: ElevenLabs'
tool-call grader cannot address a GET tool's query parameter. `q`, `query_params.q`, `query.q`
and `query_params_schema.q` all report "Parameter path … not found", while body fields work as
`body.<field>` (`draw_spread_smash` uses `body.call`). The agent's `q` behaviour is covered by
`tests/api/test_voice_tools.py`. debt: no live check of the search query; restore a `q` parameter
check if ElevenLabs documents query-parameter paths (operator ruling, wave-4 rehearsal).

`unresolved_draw_asks_hash` tells the grader to judge only the latest reply, because the scripted
"Drawing that now." in its chat history was being held against the agent; the bar on the reply
itself is unchanged.
