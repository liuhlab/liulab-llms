import base64
from typing import Any

from labllm import Model, wire
from labllm.ceiling import largest_cost
from labllm.model import Price

#: One dollar a token in and none out, so a largest cost reads as its input tokens.
INPUT_ONLY = Price(input_usd_per_token=1.0, output_usd_per_token=0.0)


def test_a_text_form_counts_one_input_token_per_byte() -> None:
    model = Model("deepseek", "m", max_output_tokens=100)
    form = wire.chat_form(model, user="héllo", system=None, schema=None)
    # [[{"role":"user","content":"héllo"}],null] is 43 bytes: é takes two.
    assert largest_cost(Price(1.0, 10.0), form) == 43 + 100 * 10.0


def test_an_image_counts_its_providers_bound_inline_or_by_url() -> None:
    picture = base64.b64encode(bytes(2_000_000)).decode()

    def tokens(*images: str) -> float:
        parts: list[dict[str, Any]] = [{"type": "text", "text": "Read this."}]
        parts += [{"type": "image_url", "image_url": {"url": url}} for url in images]
        form = wire.chat_form(Model("deepseek", "m"), user=parts, system=None, schema=None)
        return largest_cost(INPUT_ONLY, form)

    inline = tokens(f"data:image/png;base64,{picture}")
    linked = tokens("https://example.org/screenshot.png")
    text_only = tokens()
    assert inline == linked
    # Beyond DeepSeek's published bound, an image adds only the few bytes of its part's JSON.
    assert 1024 <= inline - text_only < 1024 + 40
