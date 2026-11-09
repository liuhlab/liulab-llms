"""The model value: one provider model and its settings."""

from dataclasses import dataclass

from labllm.peak import OffPeak


@dataclass(frozen=True)
class Model:
    """A provider's exact model name and the settings a call sends with it.

    The same value serves direct calls and passes, and is stamped on every answer. The
    gateway has no aliases, so `name` is exactly what the provider calls the model.

    Examples
    --------
    >>> Model("deepseek", "deepseek-v4-flash", thinking=False, max_output_tokens=2000)
    Model(provider='deepseek', name='deepseek-v4-flash', thinking=False, effort=None, temperature=None, max_output_tokens=2000)
    """

    provider: str
    name: str
    thinking: bool | None = None
    effort: str | None = None
    temperature: float | None = None
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class Price:
    """What a model costs, in dollars per token at its dearest hour, and its off-peak hours."""

    input_usd_per_token: float
    output_usd_per_token: float
    off_peak: OffPeak | None = None
