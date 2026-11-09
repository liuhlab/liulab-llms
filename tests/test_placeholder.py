"""The placeholder's own test. Rename it with the module."""

from labllm import __version__, greet


def test_greet_addresses_the_name_it_is_given() -> None:
    assert greet("lab") == "Hello, lab!"


def test_greet_falls_back_to_the_world() -> None:
    assert greet() == "Hello, world!"


def test_version_comes_from_the_installed_metadata() -> None:
    # hatch-vcs derives it from git, so the value moves. That it exists is the claim.
    assert __version__
