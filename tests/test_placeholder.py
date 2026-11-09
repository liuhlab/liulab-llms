"""The placeholder's own test. Rename it with the module."""

from typer.testing import CliRunner

from newpkg import __version__, greet
from newpkg.cli import app


def test_greet_addresses_the_name_it_is_given() -> None:
    assert greet("lab") == "Hello, lab!"


def test_greet_falls_back_to_the_world() -> None:
    assert greet() == "Hello, world!"


def test_version_comes_from_the_installed_metadata() -> None:
    # hatch-vcs derives it from git, so the value moves. That it exists is the claim.
    assert __version__


def test_the_cli_verb_prints_the_greeting() -> None:
    result = CliRunner().invoke(app, ["greet", "lab"])
    assert result.exit_code == 0
    assert result.output.strip() == "Hello, lab!"
