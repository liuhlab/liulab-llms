"""The suite budget, the per-test timeout and the slow lane, through pytest itself.

Each test runs pytest over a scratch tree carrying this repo's own `conftest.py` and
`[tool.pytest.ini_options]`, with the two limits shrunk so a short sleep crosses them.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[1]
MANIFEST = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

SLOW_TEST = """
import time

import pytest


@pytest.mark.slow
def test_takes_its_time():
    time.sleep(0.8)
"""


def scratch(
    pytester: pytest.Pytester,
    tests: str,
    *,
    timeout: float,
    budget: float,
    where: str = "tests/test_scratch.py",
) -> None:
    """A tree with this repo's pytest configuration, these limits, and one test file."""
    ini = dict(MANIFEST["tool"]["pytest"]["ini_options"], timeout=timeout, suite_budget=budget)
    lines = "".join(f"{key} = {json.dumps(value)}\n" for key, value in ini.items())
    pytester.makepyprojecttoml(f"[tool.pytest.ini_options]\n{lines}")
    pytester.makeconftest((REPO / "conftest.py").read_text(encoding="utf-8"))
    path = pytester.path / where
    path.parent.mkdir(parents=True)
    path.write_text(tests, encoding="utf-8")


def slow_lane() -> list[str]:
    """The arguments `pixi run test-slow` hands pytest."""
    command = shlex.split(MANIFEST["tool"]["pixi"]["tasks"]["test-slow"])
    assert command[0] == "pytest"
    return command[1:]


def test_a_suite_inside_its_budget_passes_and_says_so(pytester: pytest.Pytester) -> None:
    scratch(pytester, "def test_quick():\n    pass\n", timeout=1, budget=30)
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["suite time: * s of its 30 s budget"])


class SlowFinish:
    """Session-finish work that runs before the budget's, as pytest's temp-folder clean-up does."""

    def pytest_sessionfinish(self) -> None:
        time.sleep(0.8)


def test_the_budget_stops_where_the_tests_end(pytester: pytest.Pytester) -> None:
    scratch(pytester, "def test_quick():\n    pass\n", timeout=1, budget=0.75)
    result = pytester.runpytest(plugins=[SlowFinish()])
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["suite time: * s of its 0.75 s budget"])


# An unmarked test is not exempted by a parametrize id or a directory named `slow`.
@pytest.mark.parametrize("where", ["tests/test_scratch.py", "tests/slow/test_scratch.py"])
def test_a_suite_past_its_budget_fails_the_run(pytester: pytest.Pytester, where: str) -> None:
    tests = """
import time

import pytest


@pytest.mark.parametrize("case", ["slow"])
def test_sleeps(case):
    time.sleep(0.3)
"""
    scratch(pytester, tests, timeout=1, budget=0.1, where=where)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["suite time: * s, over its 0.1 s budget*"])


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_a_test_past_the_timeout_fails_in_any_phase(pytester: pytest.Pytester, phase: str) -> None:
    tests = f"""
import time

import pytest


@pytest.fixture
def phases():
    if "{phase}" == "setup":
        time.sleep(0.5)
    yield
    if "{phase}" == "teardown":
        time.sleep(0.5)


def test_sleeps(phases):
    if "{phase}" == "call":
        time.sleep(0.5)
"""
    scratch(pytester, tests, timeout=0.1, budget=30)
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*Timeout*"])


def test_a_slow_test_is_deselected_by_default(pytester: pytest.Pytester) -> None:
    scratch(pytester, SLOW_TEST + "\n\ndef test_quick():\n    pass\n", timeout=0.2, budget=0.6)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, deselected=1)
    assert result.ret == pytest.ExitCode.OK


def test_the_slow_lane_holds_a_slow_test_to_neither_limit(pytester: pytest.Pytester) -> None:
    scratch(pytester, SLOW_TEST, timeout=0.2, budget=0.6)
    result = pytester.runpytest(*slow_lane())
    result.assert_outcomes(passed=1)
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["suite time: not checked, because slow tests ran"])


#: How long the scratch tree's database takes to start.
START_S = 0.4

GATEWAY_TEST = """
import pytest

from gateway_harness import database


@pytest.fixture
def conninfo():
    return database()


def test_uses_the_database(conninfo):
    assert conninfo == "stand-in"
"""


def gateway_scratch(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    tests: str,
    *,
    timeout: float,
    budget: float,
) -> list[str]:
    """`scratch` plus this repo's gateway test set-up, its database `START_S` slow to start.

    Returns the list each start of the database appends to.
    """
    scratch(pytester, tests, timeout=timeout, budget=budget)
    for name in ("conftest.py", "gateway_harness.py"):
        source = (REPO / "tests" / name).read_text(encoding="utf-8")
        (pytester.path / "tests" / name).write_text(source, encoding="utf-8")
    starts: list[str] = []

    @contextmanager
    def slow_start() -> Iterator[str]:
        starts.append("started")
        time.sleep(START_S)
        yield "stand-in"

    # The scratch tree's own copy of the harness, so no database this session started is reused.
    monkeypatch.delitem(sys.modules, "gateway_harness", raising=False)
    monkeypatch.setattr("labllm.server.throwaway.throwaway_database", slow_start)
    monkeypatch.setattr("labllm.server.prices.litellm", lambda: None)
    return starts


def test_the_gateway_tests_start_up_is_charged_to_the_suite_not_to_a_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A budget the run keeps without the start-up, and crosses only with it.
    starts = gateway_scratch(pytester, monkeypatch, GATEWAY_TEST, timeout=0.1, budget=0.3)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["suite time: * s, over its 0.3 s budget*"])
    assert starts == ["started"]


def test_a_run_with_no_gateway_test_starts_no_database(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = gateway_scratch(
        pytester, monkeypatch, "def test_quick():\n    pass\n", timeout=1, budget=30
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
    assert starts == []
