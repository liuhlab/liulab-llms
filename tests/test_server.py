"""The gateway server through its HTTP route, beyond what the shared call-layer suite covers."""

import dataclasses
import functools
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import openai
import psycopg
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from gateway_harness import database, empty, read_record, running, stand_in
from harness import CHAT, HOUR, MONDAY, PEAK, PRICES
from labllm import (
    CeilingReached,
    ChatReply,
    Gateway,
    JevReply,
    Model,
    ProviderExhausted,
    ProviderUnavailable,
    Rejected,
)
from labllm.client import HttpTransport
from labllm.peak import MARGIN_S
from labllm.retry import BUDGET_S
from labllm.server import Attempt, Prices, Provider, Record, Server
from labllm.server.chat import LiteLLMChat
from labllm.server.jev import TypeSafeJev
from labllm.server.limits import RateLimit
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
    conninfo: str,
    providers: Mapping[str, Provider],
    prices: Mapping[str, Price] = PRICES,
    clock: FakeClock | None = None,
    **settings: Any,
) -> Iterator[Gateway]:
    """A client of a fresh gateway on a fake clock; `settings` go to `Server`."""
    clock = clock or FakeClock()
    with Record(conninfo) as record:
        server = Server(record, Prices(prices), providers, clock=clock, **settings)
        with running(server) as url:
            yield Gateway(repo="tests", person="ann", transport=HttpTransport(url), clock=clock)


def send(
    conninfo: str, provider: Provider, model: Model, *, schema: dict[str, Any] | None = None
) -> ChatReply:
    """Send one chat call through a fresh gateway whose chat provider is `provider`."""
    with gateway(conninfo, {"chat": provider}) as gw, gw.meter("one", ceiling_usd=1.0) as m:
        return m.chat(model, user="answer in json", schema=schema)


class Completion:
    """Plays `litellm.completion`: keeps what it was sent and answers with LiteLLM's own mock."""

    def __init__(self, text: str | list[str] = '{"n": 1}', error: Exception | None = None) -> None:
        self.texts = [text] if isinstance(text, str) else text
        self.error = error
        self.sent: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if self.error is not None:
            raise self.error
        text = self.texts[min(len(self.sent), len(self.texts)) - 1]
        # LiteLLM takes an empty mock for no mock at all, so an empty reply is emptied after.
        response = litellm().completion(
            model=kwargs["model"], messages=kwargs["messages"], mock_response=text or "-"
        )
        response.choices[0].message.content = text
        return response


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


def test_rate_limits_and_the_retry_budget_are_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        "retry_budget_minutes = 3\n"
        "[limits.typesafe]\n"
        "requests_per_second = 80\n"
        "input_tokens_per_second = 100_000\n"
        "[limits.deepseek]\n"
        "calls_at_once = 32\n"
        '[limits.openai."gpt-5.5"]\n'
        "requests_per_second = 166.7\n"
    )
    settings = load(path)
    assert settings.retry_budget_s == 180.0
    assert settings.limits == {
        "typesafe": RateLimit(requests_per_second=80, input_tokens_per_second=100_000),
        "deepseek": RateLimit(calls_at_once=32),
        "openai/gpt-5.5": RateLimit(requests_per_second=166.7),
    }
    assert (load(None).limits, load(None).retry_budget_s) == ({}, BUDGET_S)


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


def test_an_empty_reply_is_paid_and_tried_again(conninfo: str) -> None:
    completion = Completion(text=["", '{"n": 1}'])
    with (
        gateway(conninfo, {"chat": LiteLLMChat(Prices(), completion=completion)}) as gw,
        gw.meter("empty", ceiling_usd=1.0) as m,
    ):
        r = m.chat(Model("openai", "gpt-5.5"), user="hello")
    # LiteLLM's mock reports 10 input and 20 output tokens for each, empty or not.
    each = 10 * 5e-6 + 20 * 30e-6
    assert (r.text, r.attempts) == ('{"n": 1}', 2)
    assert (r.cost_usd, r.first_cost_usd, m.spent_usd) == pytest.approx((2 * each, each, 2 * each))
    assert [(e.error, e.cost_usd) for e in read_record(conninfo)] == [
        ("provider_unavailable", pytest.approx(each)),
        (None, pytest.approx(each)),
    ]


def sent_at(clock: FakeClock, times: list[tuple[str, float]], text: str = "abc") -> Provider:
    """A chat provider answering `text` that keeps each attempt's user message and time."""

    def fn(form: dict[str, Any]) -> str:
        times.append((form["messages"][-1]["content"], clock.now()))
        return text

    return stand_in(fn, PRICES)


def test_calls_over_a_providers_request_rate_wait_their_turn(conninfo: str) -> None:
    clock, times = FakeClock(), []
    limits = {"openai": RateLimit(requests_per_second=2)}
    with (
        gateway(conninfo, {"chat": sent_at(clock, times)}, clock=clock, limits=limits) as gw,
        gw.meter("rate", ceiling_usd=1.0) as m,
    ):
        replies = [m.chat(CHAT, user=f"doc {i}") for i in range(4)]
    assert [t for _, t in times] == [0.0, 0.0, 0.5, 1.0]
    assert all(r.attempts == 1 for r in replies)
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute("SELECT attempt, waited_s FROM exchanges ORDER BY id").fetchall()
    assert rows == [(1, 0.0), (1, 0.0), (1, 0.5), (1, 0.5)]


def test_input_tokens_are_estimated_before_sending_and_corrected_from_usage(
    conninfo: str,
) -> None:
    clock, times = FakeClock(), []

    def counted(form: dict[str, Any]) -> Attempt:
        times.append(("", clock.now()))
        return Attempt(output="abc", usage={}, cost_usd=0.0, input_tokens=10)

    # A model's own limit; 400 bytes estimate as 100 tokens, which the provider counts as 10.
    limits = {"openai/test-chat": RateLimit(input_tokens_per_second=100)}
    with (
        gateway(conninfo, {"chat": counted}, clock=clock, limits=limits) as gw,
        gw.meter("tokens", ceiling_usd=1.0) as m,
    ):
        m.chat(CHAT, user="x" * 400)
        m.chat(CHAT, user="y" * 400)
    # 90 tokens came back with the correction; the second call waits for 10 more.
    assert [t for _, t in times] == pytest.approx([0.0, 0.1])


def test_calls_over_the_limit_on_calls_at_once_wait_first_come_first_served(
    conninfo: str,
) -> None:
    clock, order = FakeClock(), []
    started, release = threading.Event(), threading.Event()

    def slow(form: dict[str, Any]) -> str:
        order.append(form["messages"][-1]["content"])
        if order[-1] == "first":
            started.set()
            assert release.wait(timeout=10)
        return "abc"

    limits = {"openai": RateLimit(calls_at_once=1)}
    with (
        gateway(conninfo, {"chat": stand_in(slow, PRICES)}, clock=clock, limits=limits) as gw,
        gw.meter("at once", ceiling_usd=1.0) as m,
        ThreadPoolExecutor(6) as pool,
    ):
        calls = [pool.submit(m.chat, CHAT, user="first")]
        assert started.wait(timeout=10)
        for i in range(5):
            calls.append(pool.submit(m.chat, CHAT, user=f"next {i}"))
            clock.until_waiting(i + 1)
        assert order == ["first"]
        release.set()
        assert all(c.result(timeout=10).text == "abc" for c in calls)
    assert order == ["first", "next 0", "next 1", "next 2", "next 3", "next 4"]


def test_a_waiting_call_holds_none_of_its_meters_ceiling(conninfo: str) -> None:
    clock, started, release = FakeClock(), threading.Event(), threading.Event()

    def slow(form: dict[str, Any]) -> str:
        if form["messages"][-1]["content"] == "slow":
            started.set()
            assert release.wait(timeout=10)
        return "abc"

    limits = {"openai": RateLimit(calls_at_once=1)}
    providers = {"chat": stand_in(slow, PRICES)}
    with (
        gateway(
            conninfo, providers, {**PRICES, PEAK.name: PRICES[CHAT.name]}, clock, limits=limits
        ) as gw,
        ThreadPoolExecutor(2) as pool,
    ):
        busy = pool.submit(gw.meter("other", ceiling_usd=1.0).chat, CHAT, user="slow")
        assert started.wait(timeout=10)
        m = gw.meter("tight", ceiling_usd=0.15)  # room for one call's 0.10 hold, not two
        waiting = pool.submit(m.chat, CHAT, user="waits")
        clock.until_waiting(1)
        assert m.chat(PEAK, user="another provider").text == "abc"
        release.set()
        assert (busy.result(timeout=10).text, waiting.result(timeout=10).text) == ("abc", "abc")


def test_overload_pauses_that_provider_for_every_caller(conninfo: str) -> None:
    clock, times = FakeClock(), []

    def busy(form: dict[str, Any]) -> str:
        times.append((form["messages"][-1]["content"], clock.now()))
        if times[-1][0] == "first":
            raise ProviderUnavailable("overloaded", status=529, retry_after_s=600)
        return "abc"

    prices = {**PRICES, PEAK.name: PRICES[CHAT.name]}
    providers = {"chat": stand_in(busy, prices)}
    with (
        gateway(conninfo, providers, prices, clock, retry_budget_s=60) as gw,
        gw.meter("pause", ceiling_usd=1.0) as m,
    ):
        with pytest.raises(ProviderUnavailable, match="overloaded"):
            m.chat(CHAT, user="first")  # asked to wait past its budget: unavailable at once
        m.chat(PEAK, user="another provider")
        m.chat(CHAT, user="second")
    assert times == [("first", 0.0), ("another provider", 0.0), ("second", 600.0)]


class ProviderApi:
    """Plays a chat provider's HTTP API under LiteLLM, answering every request with one error.

    Keeps each request's time on `clock`.
    """

    def __init__(
        self,
        clock: FakeClock,
        provider: str,
        status: int,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        self.clock, self.provider = clock, provider
        self.status, self.body, self.headers = status, body, headers or {}
        self.sent_at: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent_at.append(self.clock.now())
        return httpx.Response(self.status, json=self.body, headers=self.headers)

    def chat(self) -> LiteLLMChat:
        """The real chat provider, with LiteLLM's HTTP client on this."""
        http = httpx.Client(transport=httpx.MockTransport(self))
        if self.provider == "openai":
            client: Any = openai.OpenAI(api_key="test-key", http_client=http, max_retries=0)
        else:
            client = HTTPHandler(client=http)
        return LiteLLMChat(
            Prices(), completion=functools.partial(litellm().completion, client=client)
        )


CHAT_MODELS = {
    "openai": Model("openai", "gpt-5.5"),
    "deepseek": Model("deepseek", DEEPSEEK),
    "anthropic": Model("anthropic", "claude-opus-4-1"),
}


@pytest.fixture
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "test-key")


def call_through(conninfo: str, api: ProviderApi, **settings: Any) -> ChatReply:
    with (
        gateway(conninfo, {"chat": api.chat()}, {}, api.clock, **settings) as gw,
        gw.meter("one", ceiling_usd=1.0) as m,
    ):
        return m.chat(CHAT_MODELS[api.provider], user="hello")


@pytest.mark.parametrize(
    ("provider", "status", "body"),
    [
        (
            "openai",
            429,
            {
                "error": {
                    "message": "You exceeded your current quota, please check your plan and "
                    "billing details.",
                    "type": "insufficient_quota",
                    "param": None,
                    "code": "insufficient_quota",
                }
            },
        ),
        ("deepseek", 402, {"error": {"message": "Insufficient Balance", "type": "unknown_error"}}),
        (
            "anthropic",
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            },
        ),
        ("anthropic", 402, {"type": "error", "error": {"type": "billing_error", "message": "no"}}),
    ],
    ids=["openai-quota-429", "deepseek-balance-402", "anthropic-credit-400", "anthropic-402"],
)
@pytest.mark.usefixtures("provider_keys")
def test_a_used_up_balance_or_cap_is_exhausted_and_never_retried(
    conninfo: str, provider: str, status: int, body: dict[str, Any]
) -> None:
    api = ProviderApi(FakeClock(), provider, status, body, {"retry-after": "1"})
    with pytest.raises(ProviderExhausted):
        call_through(conninfo, api)
    assert len(api.sent_at) == 1
    assert [e.error for e in read_record(conninfo)] == ["provider_exhausted"]


@pytest.mark.parametrize(
    ("provider", "status", "body"),
    [
        ("deepseek", 401, {"error": {"message": "Authentication Fails", "type": "auth"}}),
        ("anthropic", 401, {"type": "error", "error": {"type": "authentication_error"}}),
        ("anthropic", 400, {"type": "error", "error": {"type": "invalid_request_error"}}),
    ],
    ids=["deepseek-401", "anthropic-401", "anthropic-400"],
)
@pytest.mark.usefixtures("provider_keys")
def test_a_malformed_or_unauthorized_request_is_rejected_and_never_retried(
    conninfo: str, provider: str, status: int, body: dict[str, Any]
) -> None:
    api = ProviderApi(FakeClock(), provider, status, body)
    with pytest.raises(Rejected):
        call_through(conninfo, api)
    assert len(api.sent_at) == 1


@pytest.mark.parametrize(
    ("provider", "status", "body", "headers", "wait"),
    [
        (
            "openai",
            429,
            {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}},
            {"retry-after-ms": "1500"},
            1.5,
        ),
        (
            "anthropic",
            529,
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
            {"retry-after": "3"},
            3.0,
        ),
        ("deepseek", 503, {"error": {"message": "Server overloaded"}}, {}, 1.0),
    ],
    ids=["openai-rate-429", "anthropic-overloaded-529", "deepseek-busy-503"],
)
@pytest.mark.usefixtures("provider_keys")
def test_overload_is_tried_again_after_the_wait_the_provider_asks_for(
    conninfo: str,
    provider: str,
    status: int,
    body: dict[str, Any],
    headers: dict[str, str],
    wait: float,
) -> None:
    api = ProviderApi(FakeClock(), provider, status, body, headers)
    with pytest.raises(ProviderUnavailable):
        call_through(conninfo, api, retry_budget_s=10)
    assert len(api.sent_at) > 1
    assert api.sent_at[1] - api.sent_at[0] == wait


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
    """Plays Jev's HTTP API under TypeSafe's SDK: keeps each request, answers each the same.

    With `times`, only the first `times` requests get that answer, and the rest `JEV_BODY`.
    """

    def __init__(
        self,
        status: int = 200,
        body: bytes = JEV_BODY,
        error: Exception | None = None,
        *,
        times: int | None = None,
        retry_after: str = "0",
    ) -> None:
        self.status, self.body, self.error = status, body, error
        self.times, self.retry_after = times, retry_after
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        # Retry-After 0: were the SDK's own retries on, they would come at once and be counted.
        headers = {"content-type": "application/json", "retry-after": self.retry_after}
        if self.times is not None and len(self.requests) > self.times:
            return httpx2.Response(200, content=JEV_BODY, headers=headers)
        if self.error is not None:
            raise self.error
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
        JevApi(529, b'{"error": "overloaded"}', times=1),
        JevApi(429, b'{"error": "rate limited"}', times=1),
        JevApi(503, b"", times=1),
        JevApi(error=httpx2.ConnectError("refused"), times=1),
        JevApi(
            body=b'{"model": "jev-1.14.0", "answers": {},'
            b' "usage": {"input_tokens": 9, "output_tokens": 0}}',
            times=1,
        ),
    ],
    ids=["overloaded", "rate-limited", "unavailable", "unreachable", "no-answers"],
)
def test_a_jev_failure_is_tried_again_by_the_gateway_alone(conninfo: str, api: JevApi) -> None:
    r = ask(conninfo, api.jev())
    assert (r.attempts, len(api.requests)) == (2, 2)  # the SDK tried nothing again itself
    assert [e.error for e in read_record(conninfo)] == ["provider_unavailable", None]


def test_jevs_used_up_balance_is_exhausted_and_never_retried(conninfo: str) -> None:
    api = JevApi(402, b'{"detail": "insufficient balance"}')
    with pytest.raises(ProviderExhausted):
        ask(conninfo, api.jev())
    assert len(api.requests) == 1


def test_jev_is_tried_again_after_the_wait_it_asks_for(conninfo: str) -> None:
    api = JevApi(429, b'{"error": "rate limited"}', times=1, retry_after="2")
    assert ask(conninfo, api.jev()).attempts == 2
    with psycopg.connect(conninfo) as conn:
        rows = conn.execute("SELECT attempt, waited_s FROM exchanges ORDER BY id").fetchall()
    assert rows == [(1, 0.0), (2, 2.0)]


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
