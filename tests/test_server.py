"""The gateway server through its HTTP route, beyond what the shared call-layer suite covers."""

import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from gateway_harness import database, empty, read_record, running, stand_in
from harness import CHAT, PRICES
from labllm import (
    CeilingReached,
    ChatReply,
    Gateway,
    Model,
    ProviderUnavailable,
    Rejected,
)
from labllm.client import HttpTransport
from labllm.server import Prices, Provider, Record, Server
from labllm.server.chat import LiteLLMChat
from labllm.server.prices import litellm
from labllm.testing import FakeClock, Price

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
