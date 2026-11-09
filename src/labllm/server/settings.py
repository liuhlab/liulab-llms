"""The gateway's settings, read from a TOML file on its machine."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from labllm.model import Price
from labllm.peak import MARGIN_S


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
    """

    database: str | None = None
    prices: dict[str, Price] = field(default_factory=dict)
    peak_margin_s: float = MARGIN_S


def load(path: Path | None) -> Settings:
    """Read settings from `path`; no path gives the defaults.

    Examples
    --------
    A settings file::

        database = "dbname=labllm"
        peak_margin_minutes = 5

        [prices."my-model"]
        input_usd_per_million = 0.5
        output_usd_per_million = 2.0
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
    return Settings(database=data.get("database"), prices=prices, peak_margin_s=margin)
