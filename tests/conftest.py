from collections.abc import Iterator

import pytest

from gateway_harness import database, empty, gateway_harness
from harness import PRICES, Harness, HarnessFactory, fake_harness
from labllm.server.prices import litellm

#: Every gateway the shared call-layer suite runs against.
HARNESSES: dict[str, HarnessFactory] = {"fake": fake_harness, "gateway": gateway_harness}


@pytest.fixture(params=sorted(HARNESSES))
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    with HARNESSES[request.param](PRICES) as h:
        yield h


@pytest.fixture
def conninfo() -> str:
    db = database()
    empty(db)
    return db


def pytest_collection_finish(session: pytest.Session) -> None:
    """Pay the gateway tests' one-time start-up before any test's timeout starts.

    The suite budget's clock already runs.
    """
    if not session.config.option.collectonly and any(map(_uses_database, session.items)):
        database()
        litellm()


def _uses_database(item: pytest.Item) -> bool:
    """Whether `item` runs on the session's database, through `harness` or `conninfo`."""
    callspec = getattr(item, "callspec", None)
    on_gateway = callspec is not None and callspec.params.get("harness") == "gateway"
    return on_gateway or "conninfo" in getattr(item, "fixturenames", ())
