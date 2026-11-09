"""The gateway server through its HTTP route, beyond what the shared call-layer suite covers."""

import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest

from gateway_harness import database, empty, read_record, running, stand_in
from harness import CHAT, HOUR, MONDAY, PRICES
from labllm import (
    CeilingReached,
    ChatReply,
    Gateway,
    JevReply,
    Model,
    ProviderUnavailable,
    Rejected,
)
from labllm.client import HttpTransport
from labllm.peak import MARGIN_S
from labllm.server import Prices, Provider, Record, Server
from labllm.server.chat import LiteLLMChat
from labllm.server.jev import TypeSafeJev
from labllm.server.prices import litellm
from labllm.server.settings import load
from labllm.testing import FakeClock, Price

DEEPSEEK = "deepseek-v4-flash"
SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


@pytest.fixture
def conninfo() -> str:
    db = database()
    empty(db)
    return db


@contextmanager
def gateway(
    conninfo: str, providers: Mapping[str, Provider], prices: Mapping[str, Price] = PRICES
) -> Iterator[Gateway]:
    clock = FakeClock()
    with Record(conninfo) as record, running(Server(record, Prices(prices), providers)) as url:
        yield Gateway(repo="tests", person="ann", transport=HttpTransport(url), clock=clock)


def send(
    conninfo: str, provider: Provider, model: Model, *, schema: dict[str, Any] | None = None
) -> ChatReply:
    """Send one chat call through a fresh gateway whose chat provider is `provider`."""
    with gateway(conninfo, {"chat": provider}) as gw, gw.meter("one", ceiling_usd=1.0) as m:
        return m.chat(model, user="answer in json", schema=schema)


class Completion:
    """Plays `litellm.completion`: keeps what it was sent and answers with LiteLLM's own mock."""

    def __init__(self, text: str = '{"n": 1}', error: Exception | None = None) -> None:
        self.text, self.error = text, error
        self.sent: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if self.error is not None:
            raise self.error
        return litellm().completion(
            model=kwargs["model"], messages=kwargs["messages"], mock_response=self.text
        )


def test_every_attempt_is_kept_whole_with_its_labels(conninfo: str) -> None:
    provider = stand_in(lambda form: "abc", PRICES)
    with gateway(conninfo, {"chat": provider}) as gw, gw.meter("pass", ceiling_usd=1.0) as m:
        m.chat(CHAT, system="be brief", user="hello", tag="GSE1")
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT form, reply, usage, cost_usd, person, repo, meter, label, tag FROM exchanges"
        ).fetchone()
    assert row is not None
    form, reply, usage, cost, *labels = row
    assert form["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert (reply, usage, cost) == (
        {"text": "abc"},
        {"input_tokens": 13, "output_tokens": 3},
        pytest.approx(0.03),
    )
    assert labels == ["ann", "tests", m.id, "pass", "GSE1"]


def test_a_retired_entry_stays_in_the_record_and_the_next_call_pays(conninfo: str) -> None:
    provider = stand_in(lambda form: "abc", PRICES)
    with gateway(conninfo, {"chat": provider}) as gw, gw.meter("retire", ceiling_usd=1.0) as m:
        m.chat(CHAT, user="hello")
        key = read_record(conninfo)[0].key
        # How an admin retires an entry by hand.
        with psycopg.connect(conninfo) as conn:
            conn.execute(
                "UPDATE exchanges SET retired_at = now() WHERE key = %s AND retired_at IS NULL",
                (key,),
            )
        again = m.chat(CHAT, user="hello")
        third = m.chat(CHAT, user="hello")
    assert (again.cached, third.cached) == (False, True)
    assert [(e.key, e.cached) for e in read_record(conninfo)] == [
        (key, False),
        (key, False),
        (key, True),
    ]


def test_a_restart_keeps_each_meters_spend(conninfo: str) -> None:
    provider = stand_in(lambda form: "abc", PRICES)
    with (
        Record(conninfo) as record,
        running(Server(record, Prices(PRICES), {"chat": provider})) as url,
    ):
        gw = Gateway(repo="tests", transport=HttpTransport(url), clock=FakeClock())
        m = gw.meter("long pass", ceiling_usd=0.12)
        m.chat(CHAT, user="one")  # spends 0.03
    port = int(url.rsplit(":", 1)[1])
    with (
        Record(conninfo) as record,
        running(Server(record, Prices(PRICES), {"chat": provider}), port),
        pytest.raises(CeilingReached),
    ):
        m.chat(CHAT, user="two")  # 0.03 spent before the restart, plus 0.10 held


def test_a_model_the_settings_do_not_price_is_priced_from_the_pinned_table(conninfo: str) -> None:
    calls: list[str] = []
    provider = stand_in(lambda form: calls.append("sent") or "abc", {"gpt-5.5": Price(0, 0)})
    model = Model("openai", "gpt-5.5", max_output_tokens=1000)  # at most 0.03 USD of output
    with gateway(conninfo, {"chat": provider}, prices={}) as gw:
        with gw.meter("too small", ceiling_usd=0.02) as m, pytest.raises(CeilingReached):
            m.chat(model, user="hello")
        with gw.meter("enough", ceiling_usd=0.05) as m:
            m.chat(model, user="hello")
    assert calls == ["sent"]


def test_deepseeks_peak_hours_come_from_the_pinned_table(conninfo: str) -> None:
    clock = FakeClock(MONDAY + 2 * HOUR)  # in DeepSeek's 01:00-04:00 UTC peak, by the spec
    sent_at: list[float] = []
    provider = stand_in(lambda form: sent_at.append(clock.now()) or "abc", {DEEPSEEK: Price(0, 0)})
    server = Server(record := Record(conninfo), Prices(), {"chat": provider}, clock=clock)
    with record, running(server) as url:
        gw = Gateway(repo="tests", transport=HttpTransport(url), clock=clock)
        with gw.meter("night", ceiling_usd=1.0, off_peak_only=True) as m:
            m.chat(Model("deepseek", DEEPSEEK, max_output_tokens=10), user="hello")
    assert sent_at == [MONDAY + 4 * HOUR]


def test_the_peak_margin_is_a_setting(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("peak_margin_minutes = 10\n")
    assert (load(path).peak_margin_s, load(None).peak_margin_s) == (600.0, MARGIN_S)


def test_deepseek_gets_thinking_off_its_effort_and_json_mode(conninfo: str) -> None:
    completion = Completion()
    model = Model("deepseek", "deepseek-v4-flash", thinking=False, effort="max", temperature=0)
    r = send(conninfo, LiteLLMChat(Prices(), completion=completion), model, schema=SCHEMA)
    (sent,) = completion.sent
    assert r.text == '{"n": 1}'
    assert sent["model"] == "deepseek/deepseek-v4-flash"
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["extra_body"] == {"reasoning_effort": "max"}
    assert sent["response_format"] == {"type": "json_object"}
    assert (sent["temperature"], sent["max_tokens"]) == (0.0, 4096)


def test_openai_gets_one_attempt_and_native_schema_output(conninfo: str) -> None:
    completion = Completion()
    model = Model("openai", "gpt-5.5", effort="low")
    send(conninfo, LiteLLMChat(Prices(), completion=completion), model, schema=SCHEMA)
    (sent,) = completion.sent
    assert sent["max_retries"] == 0
    assert sent["reasoning_effort"] == "low"
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": SCHEMA, "strict": True},
    }


def test_a_reply_carries_the_providers_usage_and_litellms_cost(conninfo: str) -> None:
    model = Model("openai", "gpt-5.5")
    r = send(conninfo, LiteLLMChat(Prices(), completion=Completion()), model)
    # LiteLLM's mock reports 10 input and 20 output tokens; gpt-5.5 lists $5 and $30 per million.
    assert r.usage == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    assert r.cost_usd == pytest.approx(10 * 5e-6 + 20 * 30e-6)


@pytest.mark.parametrize("error", ["BadRequestError", "AuthenticationError"])
def test_a_request_the_provider_refuses_as_malformed_or_unauthorized_is_rejected(
    conninfo: str, error: str
) -> None:
    refusal = getattr(litellm(), error)(message="no", model="gpt-5.5", llm_provider="openai")
    chat = LiteLLMChat(Prices(), completion=Completion(error=refusal))
    with pytest.raises(Rejected):
        send(conninfo, chat, Model("openai", "gpt-5.5"))
    assert [e.error for e in read_record(conninfo)] == ["rejected"]


def test_an_empty_reply_is_not_cached(conninfo: str) -> None:
    chat = LiteLLMChat(Prices(), completion=Completion(text=""))
    with pytest.raises(ProviderUnavailable):
        send(conninfo, chat, Model("openai", "gpt-5.5"))
    assert [e.error for e in read_record(conninfo)] == ["provider_unavailable"]


#: Jev's questions, by name, as its API takes them.
QUESTIONS = {
    "assay": {
        "type": "choice",
        "instructions": "Which assay is it?",
        "criteria": {"single-cell": None, "bulk": None},
    },
    "depth": {"type": "score", "instructions": "How deep?", "criteria": ["low", "mid", "high"]},
    "human": {"type": "noul", "instructions": "Is it human?"},
}
#: Jev's reply, byte for byte: "human" unanswered, "depth" lacking level 1, a probability with
#: more digits than a float holds, and an answer type newer than the SDK.
JEV_BODY = (
    b'{"model": "jev-1.14.0", "answers": {'
    b'"assay": {"type": "choice", "choice": "bulk", "confidence": 0.62,'
    b' "probabilities": {"single-cell": 0.12345678901234567891, "bulk": 0.8765432109876543}},'
    b' "depth": {"type": "score", "score": 1.5, "confidence": 0.5,'
    b' "legend": {"0": "low", "1": "mid", "2": "high"}, "probabilities": {"0": 0.25, "2": 0.75}},'
    b' "later": {"type": "rank", "order": ["b", "a"]}},'
    b' "usage": {"input_tokens": 120, "output_tokens": 12}}'
)
JEV_MODEL = Model("typesafe", "jev-1.14.0")


class JevApi:
    """Plays Jev's HTTP API under TypeSafe's SDK: keeps each request, answers each the same."""

    def __init__(
        self, status: int = 200, body: bytes = JEV_BODY, error: Exception | None = None
    ) -> None:
        self.status, self.body, self.error = status, body, error
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        # Retry-After 0: were the SDK's own retries on, they would come at once and be counted.
        headers = {"content-type": "application/json", "retry-after": "0"}
        return httpx2.Response(self.status, content=self.body, headers=headers)

    def jev(self) -> TypeSafeJev:
        """The real Jev provider, with this playing Jev."""
        return TypeSafeJev(Prices(), api_key="test-key", transport=httpx2.MockTransport(self))


def ask(conninfo: str, provider: Provider, model: Model = JEV_MODEL) -> JevReply:
    """Send one Jev call through a fresh gateway whose Jev provider is `provider`."""
    with (
        gateway(conninfo, {"jev": provider}, prices={}) as gw,
        gw.meter("one", ceiling_usd=1.0) as m,
    ):
        return m.jev(model, description="GSE1: bulk RNA-seq", questions=QUESTIONS)


def test_jevs_answers_pass_through_untouched_to_the_record_and_back(conninfo: str) -> None:
    api = JevApi()
    sent = json.loads(JEV_BODY)
    with (
        gateway(conninfo, {"jev": api.jev()}, prices={}) as gw,
        gw.meter("pass", ceiling_usd=1.0) as m,
    ):
        first = m.jev(JEV_MODEL, description="GSE1: bulk RNA-seq", questions=QUESTIONS)
        again = m.jev(JEV_MODEL, description="GSE1: bulk RNA-seq", questions=QUESTIONS)
    for r in (first, again):
        assert json.dumps(r.answers) == json.dumps(sent["answers"])  # key order included
        assert "human" not in r.answers
        assert r.answers["depth"]["probabilities"] == {"0": 0.25, "2": 0.75}
        assert r.answers["assay"]["probabilities"]["single-cell"] == 0.12345678901234567891
        assert r.answers["later"] == {"type": "rank", "order": ["b", "a"]}
    assert (first.cached, again.cached) == (False, True)
    assert first.usage == {"input_tokens": 120, "output_tokens": 12}
    assert first.cost_usd == pytest.approx(120 * 0.042e-6)  # the pinned table's Jev input price
    (request,) = api.requests
    assert json.loads(request.content) == {
        "state": "GSE1: bulk RNA-seq",
        "model": "jev-1.14.0",
        "questions": QUESTIONS,
    }
    with psycopg.connect(conninfo) as conn:
        row = conn.execute("SELECT reply, response FROM exchanges WHERE NOT cached").fetchone()
    assert row is not None
    assert json.dumps(row[0]) == json.dumps({"answers": sent["answers"]})
    assert row[1] == sent


@pytest.mark.parametrize(
    "api",
    [
        JevApi(529, b'{"error": "overloaded"}'),
        JevApi(429, b'{"error": "rate limited"}'),
        JevApi(503, b""),
        JevApi(error=httpx2.ConnectError("refused")),
        JevApi(
            body=b'{"model": "jev-1.14.0", "answers": {},'
            b' "usage": {"input_tokens": 9, "output_tokens": 0}}'
        ),
    ],
    ids=["overloaded", "rate-limited", "unavailable", "unreachable", "no-answers"],
)
def test_a_jev_failure_is_unavailable_after_one_attempt(conninfo: str, api: JevApi) -> None:
    with pytest.raises(ProviderUnavailable):
        ask(conninfo, api.jev())
    assert len(api.requests) == 1
    assert [e.error for e in read_record(conninfo)] == ["provider_unavailable"]


@pytest.mark.parametrize("status", [400, 401, 422])
def test_a_jev_request_refused_as_malformed_or_unauthorized_is_rejected_unretried(
    conninfo: str, status: int
) -> None:
    api = JevApi(status, b'{"detail": "no"}')
    with pytest.raises(Rejected):
        ask(conninfo, api.jev())
    assert len(api.requests) == 1
    assert [e.error for e in read_record(conninfo)] == ["rejected"]


@pytest.mark.parametrize(
    "model",
    [dataclasses.replace(JEV_MODEL, temperature=0.0), Model("openai", "jev-1.14.0")],
    ids=["a-setting-jev-does-not-take", "not-a-typesafe-model"],
)
def test_a_jev_call_jev_cannot_take_is_rejected_unsent(conninfo: str, model: Model) -> None:
    api = JevApi()
    with pytest.raises(Rejected):
        ask(conninfo, api.jev(), model)
    assert api.requests == []


def test_a_gateway_with_no_jev_key_rejects_jev_calls(
    conninfo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(Rejected, match="API key"):
        ask(conninfo, TypeSafeJev(Prices()))


def test_the_server_imports_litellm_only_to_use_it_and_never_downloads_prices() -> None:
    code = (
        "import os, sys\n"
        "from labllm.server import Prices\n"
        "before = 'litellm' in sys.modules\n"
        "Prices().price({'model': 'gpt-5.5', 'provider': 'openai'})\n"
        "print(before, os.environ['LITELLM_LOCAL_MODEL_COST_MAP'])\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.split() == ["False", "True"]
