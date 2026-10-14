"""Token Factory client, JSON helpers and the model router (all offline)."""
from types import SimpleNamespace as NS

import pytest

from ledgermind.config import load_dotenv, load_settings, missing_key_help
from ledgermind.llm import (AllModelsFailed, ChatRequest, ChatResult, LLMError, MockClient, ModelRouter,
                            TokenFactoryClient)
from ledgermind.llm.jsonutil import ThinkFilter, parse_json, strip_reasoning


@pytest.fixture
def settings():
    return load_settings(env={"LEDGERMIND_MOCK": "1"})


def echo(req: ChatRequest) -> ChatResult:
    return ChatResult(content=f"ok:{req.task}")


def test_config_has_doc_verified_ids(settings):
    ids = {alias: m.id for alias, m in settings.models.items()}
    assert ids["nano"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert ids["super"] == "nvidia/nemotron-3-super-120b-a12b"
    assert ids["ultra"] == "nvidia/Nemotron-3-Ultra-550b-a55b"
    assert [m.id for m in settings.chain("agent")][0] == ids["super"]
    assert settings.mock


def test_env_overrides_model_id():
    s = load_settings(env={"LEDGERMIND_MODEL_SUPER": "nvidia/other", "NEBIUS_API_KEY": "k"})
    assert s.models["super"].id == "nvidia/other" and not s.mock


def test_dotenv_reads_notepad_files_and_fills_empty_variables(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_bytes("\ufeffNEBIUS_API_KEY=abc123\r\nLEDGERMIND_CURRENCY=USD\r\n".encode("utf-8"))
    monkeypatch.setenv("NEBIUS_API_KEY", "")
    monkeypatch.setenv("LEDGERMIND_CURRENCY", "INR")
    load_dotenv(env_file)
    assert load_settings().api_key == "abc123"
    assert load_settings().currency == "INR"


def test_missing_key_help_points_at_the_file(tmp_path):
    assert "No .env file" in missing_key_help(tmp_path)
    (tmp_path / ".env.txt").write_text("NEBIUS_API_KEY=x", encoding="utf-8")
    assert "rename it to .env" in missing_key_help(tmp_path)
    (tmp_path / ".env").write_text("NEBIUS_API_KEY=", encoding="utf-8")
    assert "is empty in" in missing_key_help(tmp_path)


def test_strip_reasoning_and_parse_json():
    assert strip_reasoning("<think>hmm</think> Hello") == "Hello"
    assert strip_reasoning("<think>still going") == ""
    assert parse_json('sure ```json\n{"a": 1,}\n```') == {"a": 1}
    assert parse_json('{"ok": True, "x": None}') == {"ok": True, "x": None}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_think_filter_streams_only_the_answer():
    f = ThinkFilter()
    out = "".join(f.feed(p) for p in ["<thi", "nk>secret plan</th", "ink>\n\nHello ", "world<", "3"]) + f.flush()
    assert out == "Hello world<3"


def test_router_falls_back_to_next_model(settings):
    super_id = settings.models["super"].id
    client = MockClient(echo, failures={super_id: [LLMError("model not found", retryable=False, status=404)]})
    router = ModelRouter(settings, client, sleep=lambda s: None)
    result, rec = router.chat("agent", ChatRequest(messages=[{"role": "user", "content": "hi"}], task="t"))
    assert result.content == "ok:t"
    assert rec.model == settings.models["lightning"].id and rec.fallback_from == [super_id]
    assert [c.ok for c in router.calls] == [False, True]


def test_router_retries_rate_limits_with_retry_after(settings):
    nano = settings.models["nano"].id
    waits = []
    client = MockClient(echo, failures={nano: [LLMError("429", retryable=True, status=429, retry_after=3)]})
    router = ModelRouter(settings, client, sleep=waits.append)
    _, rec = router.chat("fast", ChatRequest(messages=[{"role": "user", "content": "hi"}]))
    assert rec.model == nano and rec.attempts == 2 and waits == [3]


def test_router_gives_friendly_error_when_all_fail(settings):
    boom = lambda: [LLMError("down", retryable=False, status=503)]  # noqa: E731
    client = MockClient(echo, failures={m.id: boom() for m in settings.models.values()})
    router = ModelRouter(settings, client, sleep=lambda s: None)
    with pytest.raises(AllModelsFailed, match="try again"):
        router.chat("deep", ChatRequest(messages=[{"role": "user", "content": "hi"}]))


def test_router_applies_privacy_hook_and_logs_cost(settings):
    seen = []
    client = MockClient(lambda req: seen.append(req.messages) or ChatResult(content="x"))
    router = ModelRouter(settings, client, privacy_hook=lambda req: [{"role": "user", "content": "[clean]"}])
    _, rec = router.chat("fast", ChatRequest(messages=[{"role": "user", "content": "secret 4111111111111111"}]))
    assert seen == [[{"role": "user", "content": "[clean]"}]]
    assert rec.cost_usd >= 0 and rec.label == "Nemotron 3 Nano"


def test_stream_falls_back_before_first_token(settings):
    super_id = settings.models["super"].id
    client = MockClient(lambda r: ChatResult(content="hello there"),
                        failures={super_id: [LLMError("x", retryable=False, status=404)]})
    events = list(ModelRouter(settings, client).stream("agent", ChatRequest(messages=[])))
    text = "".join(e.text for e in events if getattr(e, "kind", None) == "text")
    assert text == "hello there" and events[-1].model == settings.models["lightning"].id


class _SDK:
    """Fake OpenAI SDK: replays canned responses and records each request."""

    def __init__(self, replies, reject_extra=False):
        self.replies, self.reject, self.requests = list(replies), reject_extra, []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        if self.reject and "extra_body" in kw:
            raise type("BadRequestError", (Exception,), {"status_code": 400})("unsupported")
        content, finish, calls = self.replies.pop(0)
        msg = NS(content=content, tool_calls=calls)
        return NS(choices=[NS(message=msg, finish_reason=finish)], usage=NS(prompt_tokens=10, completion_tokens=5,
                                                                             completion_tokens_details=None))


def test_client_disables_thinking_and_retries_when_cut_off(settings):
    sdk = _SDK([("", "length", None), ("ready", "stop", None)])
    res = TokenFactoryClient(settings, sdk=sdk).chat(settings.models["nano"], ChatRequest(messages=[], max_tokens=40))
    assert res.content == "ready"
    assert sdk.requests[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert sdk.requests[1]["max_tokens"] == 160


def test_client_drops_optional_params_if_rejected(settings):
    sdk = _SDK([("<think>hm</think>ok", "stop", None)], reject_extra=True)
    res = TokenFactoryClient(settings, sdk=sdk).chat(settings.models["nano"],
                                                     ChatRequest(messages=[], json_mode=True))
    assert res.content == "ok" and "response_format" not in sdk.requests[-1]


def test_client_parses_tool_calls(settings):
    call = NS(id="c1", function=NS(name="spending_summary", arguments='{"period": "last_month"}'))
    sdk = _SDK([(None, "tool_calls", [call])])
    res = TokenFactoryClient(settings, sdk=sdk).chat(settings.models["super"], ChatRequest(messages=[], tools=[{}]))
    assert res.tool_calls[0].name == "spending_summary" and sdk.requests[0]["tool_choice"] == "auto"
