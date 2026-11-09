"""The tunnel check that `docs/reach-the-gateway.md` quotes, on the fake gateway."""

from check_gateway import check

from labllm.testing import FakeGateway, Price


def test_the_check_returns_the_models_reply() -> None:
    fake = FakeGateway(lambda form: "OK", prices={"deepseek-v4-flash": Price(0.0, 1e-6)})
    assert check(fake.client()) == "OK"
