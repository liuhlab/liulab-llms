"""The contract the shared call-layer suite is written against, and the fake's harness.

A harness stands up one gateway for one test. The suite only makes clients, sets the
function that plays the provider, and reads the record, so another gateway joins the suite
by adding a factory to `HARNESSES` in `conftest.py`.

A harness must apply the fake's token rule to whatever the provider function returns: one
output token per character of the reply text, or of Jev's answers as JSON.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from labllm import Gateway, Model
from labllm.testing import Exchange, FakeClock, FakeGateway, OffPeak, Price, Provider

#: A Monday at 00:00 UTC, and an hour, for tests that set the clock against peak hours.
MONDAY = datetime(2026, 11, 9, tzinfo=UTC).timestamp()
HOUR = 3600.0

#: Off-peak hours exactly as DeepSeek's entry in LiteLLM's price table writes them.
DEEPSEEK_OFF_PEAK = {
    "windows": [
        {"hours_utc": ["00:00-01:00", "04:00-06:00", "10:00-00:00"], "weekdays": [1, 2, 3, 4, 5]},
        {"hours_utc": "00:00-00:00", "weekdays": [6, 7]},
    ]
}

#: The suite's models. Input is free, so a reservation is the output allowance alone and the
#: suite holds for any gateway whatever it estimates for input. `PEAK` has DeepSeek's hours.
CHAT = Model("openai", "test-chat", max_output_tokens=10)
JEV = Model("typesafe", "test-jev", max_output_tokens=10)
PEAK = Model("deepseek", "test-peak", max_output_tokens=10)
PRICES: Mapping[str, Price] = {
    CHAT.name: Price(input_usd_per_token=0.0, output_usd_per_token=0.01),
    JEV.name: Price(input_usd_per_token=0.0, output_usd_per_token=0.01),
    PEAK.name: Price(0.0, 0.01, off_peak=OffPeak.read(DEEPSEEK_OFF_PEAK)),
}


class Harness(Protocol):
    """One gateway, fresh for one test."""

    #: The clock the gateway and its clients share.
    clock: FakeClock

    def client(self, *, repo: str = "tests", person: str | None = None) -> Gateway:
        """A client of this gateway, on a clock the harness controls."""
        ...

    def provider(self, fn: Provider) -> None:
        """Set the function that plays the provider for every later call."""
        ...

    def record(self) -> list[Exchange]:
        """Every exchange so far, oldest first."""
        ...


#: Stands up a gateway pricing each model name in `prices`, and tears it down on exit.
HarnessFactory = Callable[[Mapping[str, Price]], AbstractContextManager[Harness]]


class FakeHarness:
    """The shared suite over `labllm.testing.FakeGateway`."""

    def __init__(self, prices: Mapping[str, Price]) -> None:
        self._provider: Provider = _unset
        self.fake = FakeGateway(lambda form: self._provider(form), prices=prices)
        self.clock = self.fake.clock

    def client(self, *, repo: str = "tests", person: str | None = None) -> Gateway:
        return self.fake.client(repo=repo, person=person)

    def provider(self, fn: Provider) -> None:
        self._provider = fn

    def record(self) -> list[Exchange]:
        return self.fake.record


@contextmanager
def fake_harness(prices: Mapping[str, Price]) -> Iterator[Harness]:
    yield FakeHarness(prices)


def _unset(form: dict[str, Any]) -> Any:
    raise AssertionError("the test set no provider function")
