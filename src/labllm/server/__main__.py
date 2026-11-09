"""Start the gateway: ``python -m labllm.server [--settings FILE] [--port N] [--throwaway]``.

Provider keys come from the environment, under the names LiteLLM reads, such as
``DEEPSEEK_API_KEY``, and ``TYPESAFE_API_KEY`` for Jev.
"""

import argparse
import logging
import signal
from contextlib import ExitStack
from pathlib import Path
from types import FrameType

from labllm.server import Prices, Record, Server
from labllm.server.chat import LiteLLMChat
from labllm.server.jev import TypeSafeJev
from labllm.server.settings import load
from labllm.server.throwaway import throwaway_database

log = logging.getLogger("labllm.server")


def main(argv: list[str] | None = None) -> None:
    """Serve until interrupted."""
    parser = argparse.ArgumentParser(prog="python -m labllm.server", description=__doc__)
    parser.add_argument("--settings", type=Path, help="a TOML settings file")
    parser.add_argument("--host", default="127.0.0.1", help="the address to listen on")
    parser.add_argument("--port", type=int, default=8000, help="the port to listen on")
    parser.add_argument(
        "--throwaway",
        action="store_true",
        help="use a new empty PostgreSQL, deleted on exit, in place of the settings' database",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _interrupt)  # a service manager's stop: clean up as on Ctrl-C
    settings = load(args.settings)
    with ExitStack() as stack:
        conninfo = settings.database or ""
        if args.throwaway:
            conninfo = stack.enter_context(throwaway_database())
        record = stack.enter_context(Record(conninfo))
        prices = Prices(settings.prices)
        providers = {"chat": LiteLLMChat(prices), "jev": TypeSafeJev(prices)}
        server = Server(
            record,
            prices,
            providers,
            peak_margin_s=settings.peak_margin_s,
            limits=settings.limits,
            retry_budget_s=settings.retry_budget_s,
        )
        httpd = server.http(args.host, args.port)
        stack.callback(httpd.server_close)
        host, port = httpd.server_address[:2]
        log.info("serving on http://%s:%s", host, port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            # A second signal must not cut short the cleanup, which stops a throwaway database.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            log.info("stopping")


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    main()
