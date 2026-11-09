"""A throwaway PostgreSQL server, for tests and for trying the gateway on one machine."""

import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def throwaway_database() -> Iterator[str]:
    """Start an empty PostgreSQL on a free local port; stop it and delete its files on exit.

    It runs with durability off, so it suits nothing that must survive. The PostgreSQL
    programs (`initdb`, `pg_ctl`) must be on the path, as the gateway's pixi environment puts
    them.

    Yields
    ------
    str
        Its connection string.
    """
    home = Path(tempfile.mkdtemp(prefix="labllm-pg-"))
    data = home / "data"
    try:
        _run("initdb", "-D", str(data), "-U", "postgres", "-A", "trust", "-E", "UTF8", "--no-sync")
        port = _free_port()
        options = f"-h 127.0.0.1 -p {port} -k '' -F -c fsync=off -c full_page_writes=off"
        _run("pg_ctl", "-D", str(data), "-l", str(home / "log"), "-o", options, "-w", "start")
        try:
            yield f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
        finally:
            _run("pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
