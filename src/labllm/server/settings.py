"""The gateway's settings, read from a TOML file on its machine."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from labllm.model import Price
from labllm.peak import MARGIN_S
from labllm.retry import BUDGET_S
from labllm.server.limits import RateLimit


@dataclass(frozen=True)
class Settings:
    """What the gateway's machine decides.

    Attributes
    ----------
    database
        The PostgreSQL connection string, or None for psycopg's defaults.
    prices
        A price for each exact model name, entered per million tokens. It wins over LiteLLM's
        pinned table.
    peak_margin_s
        New off-peak-only calls stop this many seconds before peak starts, entered in minutes.
    limits
        Rate limits by provider, or by ``"provider/model"`` for a provider that sets them per
        model, as `labllm.server.Server` takes them.
    retry_budget_s
        How long a call keeps trying an unavailable provider, entered in minutes.
    """

    database: str | None = None
    prices: dict[str, Price] = field(default_factory=dict)
    peak_margin_s: float = MARGIN_S
    limits: dict[str, RateLimit] = field(default_factory=dict)
    retry_budget_s: float = BUDGET_S


def load(path: Path | None) -> Settings:
    """Read settings from `path`; no path gives the defaults.

    Examples
    --------
    A settings file::

        database = "dbname=labllm"
        peak_margin_minutes = 5
        retry_budget_minutes = 5

        [prices."my-model"]
        input_usd_per_million = 0.5
        output_usd_per_million = 2.0

        [limits.typesafe]
        requests_per_second = 80
        input_tokens_per_second = 100_000

        [limits.openai."gpt-5.5"]
        requests_per_second = 166.7  # 10,000 a minute

    Raises
    ------
    TypeError
        A rate limit names something other than the three limits.
    """
    if path is None:
        return Settings()
    data = tomllib.loads(path.read_text())
    prices = {
        name: Price(
            float(p["input_usd_per_million"]) / 1e6, float(p["output_usd_per_million"]) / 1e6
        )
        for name, p in data.get("prices", {}).items()
    }
    margin = float(data.get("peak_margin_minutes", MARGIN_S / 60)) * 60
    limits: dict[str, RateLimit] = {}
    for provider, table in data.get("limits", {}).items():
        own = {k: v for k, v in table.items() if not isinstance(v, dict)}
        if own:
            limits[provider] = RateLimit(**own)
        for model, values in table.items():
            if isinstance(values, dict):
                limits[f"{provider}/{model}"] = RateLimit(**values)
    return Settings(
        database=data.get("database"),
        prices=prices,
        peak_margin_s=margin,
        limits=limits,
        retry_budget_s=float(data.get("retry_budget_minutes", BUDGET_S / 60)) * 60,
    )
