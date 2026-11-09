"""The client's own behaviour, over `FakeGateway` and over HTTP."""

import getpass
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from harness import CHAT, PRICES
from labllm import Gateway, GatewayUnreachable
from labllm.testing import FakeClock, FakeGateway


@pytest.fixture
def fake() -> FakeGateway:
    return FakeGateway(lambda form: "abc", prices=PRICES)


def test_the_ceiling_has_no_default(fake: FakeGateway) -> None:
    with pytest.raises(TypeError):
        fake.client().meter("no ceiling")  # type: ignore[call-arg]


@pytest.mark.parametrize("ceiling", [None, -1.0, float("nan"), float("inf"), True, "5"])
def test_the_ceiling_is_a_number_of_dollars(fake: FakeGateway, ceiling: object) -> None:
    with pytest.raises(ValueError, match="ceiling_usd"):
        fake.client().meter("bad ceiling", ceiling_usd=ceiling)  # type: ignore[arg-type]


def test_each_call_carries_its_meter_labels_caller_and_tag(fake: FakeGateway) -> None:
    with fake.client(repo="seqforge").meter("harvest", ceiling_usd=2.5, off_peak_only=True) as m:
        m.chat(CHAT, user="hello", tag="doc-7")
    (x,) = fake.record
    assert (x.meter, x.label, x.ceiling_usd, x.off_peak_only) == (m.id, "harvest", 2.5, True)
    assert (x.person, x.repo, x.tag) == (getpass.getuser(), "seqforge", "doc-7")


def test_each_meter_has_its_own_identity(fake: FakeGateway) -> None:
    client = fake.client()
    assert client.meter("same", ceiling_usd=1.0).id != client.meter("same", ceiling_usd=1.0).id


def test_a_refused_connection_is_retried_briefly(fake: FakeGateway) -> None:
    fake.refuse_connections(3)
    with fake.client().meter("restart", ceiling_usd=1.0) as m:
        assert m.chat(CHAT, user="hello").text == "abc"
    assert 0 < fake.clock.now() < Gateway.connect_retry_s


def test_a_gateway_that_stays_down_stops_the_caller(fake: FakeGateway) -> None:
    fake.refuse_connections()
    with fake.client().meter("down", ceiling_usd=1.0) as m, pytest.raises(GatewayUnreachable):
        m.chat(CHAT, user="hello")
    assert fake.clock.now() >= Gateway.connect_retry_s
    assert fake.record == []


def test_an_answer_no_client_understands_stops_the_caller() -> None:
    class Garbled:
        def send(self, path: str, body: bytes) -> tuple[int, bytes]:
            return 500, b"Internal Server Error"

    gw = Gateway(repo="tests", transport=Garbled())
    with gw.meter("garbled", ceiling_usd=1.0) as m, pytest.raises(GatewayUnreachable):
        m.chat(CHAT, user="hello")


def test_without_an_address_the_gateway_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LABLLM_GATEWAY", raising=False)
    with pytest.raises(GatewayUnreachable, match="LABLLM_GATEWAY"):
        Gateway(repo="tests")


@pytest.fixture
def served(fake: FakeGateway) -> Iterator[str]:
    """The fake gateway behind a real HTTP server on a free local port."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            status, out = fake.send(self.path, body)
            self.send_response(status)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_address_comes_from_the_environment(
    served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABLLM_GATEWAY", served)
    with Gateway(repo="tests").meter("http", ceiling_usd=1.0) as m:
        assert m.chat(CHAT, user="hello").text == "abc"
        assert m.chat(CHAT, user="hello").cached is True


def test_a_closed_port_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("LABLLM_GATEWAY", f"http://127.0.0.1:{port}")
    gw = Gateway(repo="tests", clock=FakeClock())
    with gw.meter("closed", ceiling_usd=1.0) as m, pytest.raises(GatewayUnreachable):
        m.chat(CHAT, user="hello")
