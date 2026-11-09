from collections.abc import Iterator

import pytest

from harness import PRICES, Harness, HarnessFactory, fake_harness

#: Every gateway the shared call-layer suite runs against.
HARNESSES: dict[str, HarnessFactory] = {"fake": fake_harness}


@pytest.fixture(params=sorted(HARNESSES))
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    with HARNESSES[request.param](PRICES) as h:
        yield h
