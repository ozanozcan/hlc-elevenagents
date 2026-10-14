"""Static checks on the ElevenLabs agent-as-code folder (req #550, d#1112, d#1114).

Runs in normal CI with no ElevenLabs access: it only reads ``elevenlabs/``.
Running the attached tests against the live agent is a separate, operator step.

``ELEVENLABS_DIR`` can point the checks at a copy of the folder (used to
mutation-check this file without touching tracked files).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

ELEVENLABS_DIR = Path(
    os.environ.get("ELEVENLABS_DIR") or Path(__file__).resolve().parents[2] / "elevenlabs"
)

AUTH_HEADER = {"variable_name": "secret__hlc_voice_token"}
EXPECTED_TOOLS = {
    "search_plays": "webhook",
    "get_play": "webhook",
    "draw_play": "webhook",
    "show_play": "client",
    "show_draw": "client",
}
ASR_KEYWORDS = {
    "trips", "doubles", "bunch", "empty", "smash", "mesh", "flood",
    "four verticals", "post", "corner", "dig", "curl", "Cover 2", "Cover 3",
    "quarters", "X", "Y", "Z", "H",
}  # fmt: skip
# Shapes that are never acceptable in a committed config file.
SECRET_PATTERNS = {
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "ElevenLabs API key": re.compile(r"\b(?:sk|xi)_[A-Za-z0-9]{20,}"),
    "tunnel hostname": re.compile(r"[a-z0-9-]+\.ngrok(?:-free)?\.(?:app|io|dev)"),
}


def _load(rel: str) -> Any:
    return json.loads((ELEVENLABS_DIR / rel).read_text())


def _json_files() -> list[Path]:
    return sorted(ELEVENLABS_DIR.rglob("*.json"))


def _agent() -> dict[str, Any]:
    (entry,) = _load("agents.json")["agents"]
    return _load(entry["config"])


def _tool_entries() -> list[dict[str, Any]]:
    return _load("tools.json")["tools"]


def _tool_configs() -> dict[str, dict[str, Any]]:
    return {e["name"]: _load(e["config"]) for e in _tool_entries()}


def _test_configs() -> list[dict[str, Any]]:
    return [_load(e["config"]) for e in _load("tests.json")["tests"]]


def test_every_json_file_parses() -> None:
    files = _json_files()
    assert files, f"no JSON under {ELEVENLABS_DIR}"
    bad = []
    for path in files:
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError:
            bad.append(path.name)
    assert not bad, f"invalid JSON: {bad}"


def test_agent_requires_auth() -> None:
    """A public agent lets anyone holding its id open conversations on our quota; the
    /voice page uses a backend-minted conversation token, which works with auth on."""
    assert _agent()["platform_settings"]["auth"]["enable_auth"] is True


def test_agent_llm_is_claude_haiku_4_5_without_reasoning_effort() -> None:
    prompt = _agent()["conversation_config"]["agent"]["prompt"]
    assert prompt["llm"] == "claude-haiku-4-5"  # d#1114
    # ElevenLabs rejects the whole agent update (400 "Reasoning effort is not
    # supported for this LLM") when this field is set for claude-haiku-4-5.
    assert "reasoning_effort" not in prompt


def test_registry_holds_exactly_the_five_tools_with_matching_configs() -> None:
    entries = _tool_entries()
    assert {e["name"]: e["type"] for e in entries} == EXPECTED_TOOLS
    for entry in entries:
        config = _load(entry["config"])
        assert config["name"] == entry["name"], entry["name"]
        assert config["type"] == entry["type"], entry["name"]


def test_agent_tool_ids_match_the_registered_tool_ids() -> None:
    """Every tool the agent references exists in tools.json, and vice versa.

    A tool with no id yet (not created on ElevenLabs) cannot be referenced;
    once lane Z writes its id into tools.json, this fails until the agent
    lists it too.
    """
    agent_ids = _agent()["conversation_config"]["agent"]["prompt"]["tool_ids"]
    registered = {e["id"] for e in _tool_entries() if e.get("id")}
    assert len(agent_ids) == len(set(agent_ids)), "duplicate tool id in agent"
    assert set(agent_ids) == registered


@pytest.mark.parametrize("name", [n for n, t in EXPECTED_TOOLS.items() if t == "webhook"])
def test_server_tool_carries_both_auth_headers_in_typed_form(name: str) -> None:
    """d#1112 + plan decision 15: a plain-string header is sent literally."""
    headers = _tool_configs()[name]["api_schema"].get("request_headers", {})
    assert headers.get("Authorization") == AUTH_HEADER, (
        f"{name}: Authorization must be {AUTH_HEADER}"
    )
    secret = headers.get("X-HLC-Tool-Secret")
    assert isinstance(secret, dict) and set(secret) == {"secret_id"}, (
        f"{name}: X-HLC-Tool-Secret must be a {{'secret_id': ...}} reference"
    )
    assert isinstance(secret["secret_id"], str) and secret["secret_id"], name


def test_no_header_anywhere_is_a_plain_string() -> None:
    plain = [
        f"{name}:{header}"
        for name, config in _tool_configs().items()
        for header, value in config.get("api_schema", {}).get("request_headers", {}).items()
        if not isinstance(value, dict)
    ]
    assert not plain, f"plain-string headers would be sent literally: {plain}"


def test_no_literal_secret_or_tunnel_host_in_any_file() -> None:
    hits = [
        f"{path.name}: {label}"
        for path in sorted(ELEVENLABS_DIR.rglob("*"))
        if path.is_file()
        for label, pattern in SECRET_PATTERNS.items()
        if pattern.search(path.read_text())
    ]
    assert not hits, f"literal secret-shaped values: {hits}"


def test_no_configured_secret_value_appears_in_any_file() -> None:
    """Compare against the real settings without ever printing a value."""
    from app.config import settings

    names = (
        "elevenlabs_api_key",
        "elevenlabs_tool_secret",
        "elevenlabs_webhook_secret",
        "voice_token_secret",
        "voice_tunnel_host",
    )
    values = {n: getattr(settings, n) for n in names}
    leaked = [
        f"{path.name}: {n}"
        for path in sorted(ELEVENLABS_DIR.rglob("*"))
        if path.is_file()
        for n, value in values.items()
        if len(value) >= 8 and value in path.read_text()
    ]
    assert not leaked, f"configured secret values found: {leaked}"


def test_get_play_shape() -> None:
    config = _tool_configs()["get_play"]
    api = config["api_schema"]
    assert api["method"] == "GET"
    assert api["url"].startswith("https://{{system__env_hlc_tunnel_host}}/")
    assert api["url"].endswith("/voice-tools/plays/{play_id}")
    assert api["path_params_schema"]["play_id"]["type"] == "string"


def test_draw_play_shape_and_timeout_above_the_35s_drawer_deadline() -> None:
    config = _tool_configs()["draw_play"]
    api = config["api_schema"]
    assert config["response_timeout_secs"] == 40
    assert api["method"] == "POST"
    assert api["url"] == "https://{{system__env_hlc_tunnel_host}}/voice-tools/draw"
    body = api["request_body_schema"]
    assert body["type"] == "object"
    assert set(body["properties"]) == {"call"}
    assert body["properties"]["call"]["type"] == "string"
    assert body["required"] == ["call"]


def test_show_play_keeps_optional_hash() -> None:
    params = _tool_configs()["show_play"]["parameters"]
    assert params["properties"]["hash"]["enum"] == ["left", "right"]
    assert params["required"] == ["play_id"]


def test_show_draw_shape() -> None:
    config = _tool_configs()["show_draw"]
    assert config["expects_response"] is True
    assert config["response_timeout_secs"] == 10
    assert set(config["parameters"]["properties"]) == {"draw_id"}
    assert config["parameters"]["required"] == ["draw_id"]


def test_tool_timeouts_within_elevenlabs_bounds() -> None:
    for name, config in _tool_configs().items():
        low, high = (5, 300) if config["type"] == "webhook" else (1, 120)
        assert low <= config["response_timeout_secs"] <= high, name


def test_max_duration_600s_and_asr_keywords() -> None:
    conversation_config = _agent()["conversation_config"]
    assert conversation_config["conversation"]["max_duration_seconds"] == 600
    assert set(conversation_config["asr"]["keywords"]) == ASR_KEYWORDS


def test_no_client_overrides_enabled() -> None:
    """The brief enables no overrides; per-session keywords stay out."""
    overrides = _agent().get("platform_settings", {}).get("overrides", {})

    def enabled(node: Any) -> bool:
        if isinstance(node, dict):
            return any(enabled(v) for v in node.values())
        return node is True

    assert not enabled(overrides.get("conversation_config_override", {}))


def test_prompt_and_first_message_keep_the_rules() -> None:
    agent = _agent()["conversation_config"]["agent"]
    assert agent["first_message"] == ("Which play do you want to see — or should I draw a new one?")
    prompt = agent["prompt"]["prompt"].lower()
    for needle in (
        "search_plays",
        "never invent",
        "judgment call",
        "drawing that now",
        "unresolved",
        "rate_limited",
        "timeout",
        "never read ids",
        "show_play",
        "show_draw",
        "pass hash",
        "can't reach the playbook",
        "never say the playbook is empty",
        "politely decline",
    ):
        assert needle in prompt, needle


def test_suite_has_two_next_reply_and_two_tool_call_tests() -> None:
    configs = _test_configs()
    types = [c["type"] for c in configs]
    assert types.count("llm") >= 2
    assert types.count("tool") >= 2
    names = [c["name"] for c in configs]
    assert len(names) == len(set(names))


def test_attached_tests_match_the_pushed_tests() -> None:
    """Attached ids == tests.json ids (ids appear once lane Z pushes)."""
    testing = _agent()["platform_settings"]["testing"]
    attached = [t["test_id"] for t in testing["attached_tests"]]
    pushed = {e["id"] for e in _load("tests.json")["tests"] if e.get("id")}
    assert set(attached) == pushed


def test_tool_tests_reference_a_registered_tool() -> None:
    """Use the tool id once it exists; until then the name stands in."""
    entries = {e["name"]: e for e in _tool_entries()}
    by_id = {e["id"]: e for e in entries.values() if e.get("id")}
    for config in _test_configs():
        if config["type"] != "tool":
            continue
        ref = config["tool_call_parameters"]["referenced_tool"]
        entry = by_id.get(ref["id"]) or entries.get(ref["id"])
        assert entry is not None, f"{config['name']}: unknown tool {ref['id']}"
        assert ref["id"] == entry.get("id", entry["name"]), (
            f"{config['name']}: use {entry['name']}'s id now that it exists"
        )
        assert ref["type"] == entry["type"], config["name"]


def _tool_test(tool: str) -> dict[str, Any]:
    entries = {e["name"]: e for e in _tool_entries()}
    wanted = {tool, entries[tool].get("id")}
    (config,) = [
        c
        for c in _test_configs()
        if c["type"] == "tool" and c["tool_call_parameters"]["referenced_tool"]["id"] in wanted
    ]
    return config


def _param(config: dict[str, Any], path: str) -> dict[str, Any]:
    (param,) = [p for p in config["tool_call_parameters"]["parameters"] if p["path"] == path]
    return param["eval"]


def _first_user_message(config: dict[str, Any]) -> str:
    return next(m["message"] for m in config["chat_history"] if m["role"] == "user")


def test_search_tool_test_uses_team_vocabulary() -> None:
    config = _tool_test("search_plays")
    assert _first_user_message(config) == "show our boundary smash"
    # ElevenLabs' grader cannot address a GET tool's query parameter (q, query_params.q, query.q,
    # query_params_schema.q all report "not found"; body.<field> works), so this test asserts the
    # call itself. The q behaviour is covered by tests/api/test_voice_tools.py.
    assert config["tool_call_parameters"]["parameters"] == []


def test_draw_tool_test_matches_spread_smash() -> None:
    config = _tool_test("draw_play")
    assert _first_user_message(config) == ("draw spread right hash, field mills, boundary smash")
    assert _param(config, "body.call") == {"type": "regex", "pattern": "(?i)spread.*smash"}


def test_weather_test_asserts_decline_redirect_and_no_tool_call() -> None:
    (config,) = [
        c
        for c in _test_configs()
        if c["type"] == "llm" and "weather" in _first_user_message(c).lower()
    ]
    condition = config["success_condition"].lower()
    assert "decline" in condition
    assert "playbook" in condition
    assert "does not call any tool" in condition


def test_unresolved_test_feeds_a_canned_draw_result() -> None:
    (config,) = [
        c
        for c in _test_configs()
        if c["type"] == "llm" and any(m.get("tool_results") for m in c.get("chat_history", []))
    ]
    (result,) = [r for m in config["chat_history"] for r in m.get("tool_results", [])]
    assert result["tool_name"] == "draw_play"
    assert json.loads(result["result_value"]) == {
        "status": "unresolved",
        "notes": ["Which hash is the ball on?"],
    }
    condition = config["success_condition"].lower()
    assert "hash" in condition and "single question" in condition


def test_no_trips_vocabulary_in_tests_or_first_message() -> None:
    """d#1117: the demo team holds no Trips plays."""
    texts = [json.dumps(c) for c in _test_configs()]
    texts.append(_agent()["conversation_config"]["agent"]["first_message"])
    assert not [t for t in texts if "trips" in t.lower()]
