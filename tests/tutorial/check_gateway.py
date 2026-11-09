"""Send one tiny call through the lab gateway, to check that the tunnel works."""

from labllm import Gateway, Model


def check(gateway: Gateway) -> str:
    """Return a model's reply to a one-word request."""
    model = Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=16)
    with gateway.meter("tunnel check", ceiling_usd=0.01) as m:
        return m.chat(model, user="Reply with the single word OK.").text


if __name__ == "__main__":
    print(check(Gateway(repo="tunnel-check")))
