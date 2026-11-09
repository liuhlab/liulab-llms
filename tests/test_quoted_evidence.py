"""The two gates narrowed off `docs/research/`, with the gate's own tools as the oracle.

`docs/research/` exists to quote upstream sources verbatim, and two gates used to refuse exactly
that: `ruff format` rewrites Python code blocks inside Markdown, and `MD010` rejects the hard tab
in a pasted TSV line. Both are now narrowed, and this file is what notices if either narrowing is
deleted — this repo's own research notes carry no Python fence and no hard tab, so nothing else
here can witness it.

The second test is what keeps the exemption narrow: the same quotation under `tests/` is still
reformatted. Without it, widening the exclusion to `*.md` would pass the first test too.

Two mechanics the tests depend on, both measured against these tools:

- **ruff is handed a DIRECTORY, never the file.** `force-exclude` is off by default, so a path
  named on the command line is formatted even where the configuration excludes it. `fmt-check`
  passes `.`, so a directory is what the gate does anyway.
- **markdownlint runs from the repo root.** It does not look above its working directory for a
  configuration, so the same file linted from `docs/research/` is checked by markdownlint's
  defaults and not by this repo's.

Both fixtures are written, read by the tools, and removed. Nothing is committed under
`docs/research/`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# What a research note is for: a Python fence nobody formatted, and a record whose tabs are the
# data. Everything else here is only what stops markdownlint having a second opinion.
QUOTED_EVIDENCE = (
    "# Quoted evidence\n"
    "\n"
    "The upstream source, as it is written there:\n"
    "\n"
    "```python\n"
    'd = {  "a":1 }\n'
    "```\n"
    "\n"
    "And one line of its lookup table:\n"
    "\n"
    "```text\n"
    "chr1\t100\t200\n"
    "```\n"
)


@pytest.fixture
def quotation() -> Iterator[Callable[[str], Path]]:
    """Place the quotation in one directory of this repo, and take it away again.

    It has to be this repo: both exclusions are anchored to the directory holding the
    configuration, so a copy of the tree somewhere else matches neither. `docs/research/` is
    created when absent, because `init-repo` prunes it with its last note.
    """
    written: list[Path] = []
    created: list[Path] = []

    def place(directory: str) -> Path:
        parent = REPO / directory
        if not parent.exists():
            parent.mkdir(parents=True)
            created.append(parent)
        # The pid keeps two runs of this suite out of each other's way.
        path = parent / f"quoted-evidence-{os.getpid()}.md"
        path.write_text(QUOTED_EVIDENCE, encoding="utf-8")
        written.append(path)
        return path

    yield place
    for path in written:
        path.unlink(missing_ok=True)
    for parent in created:
        parent.rmdir()


def fmt_check(directory: str) -> subprocess.CompletedProcess[str]:
    """The `fmt-check` task, scoped to one directory."""
    return subprocess.run(
        ["ruff", "format", "--check", directory],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def markdownlint() -> subprocess.CompletedProcess[str]:
    """The `markdownlint` task, which its own configuration widens to every file in the repo."""
    return subprocess.run(
        ["markdownlint-cli2"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_research_note_may_quote_code_verbatim(quotation: Callable[[str], Path]) -> None:
    """Both gates leave a note under `docs/research/` alone."""
    path = quotation("docs/research")

    formatter = fmt_check("docs/research")
    assert formatter.returncode == 0, formatter.stdout

    linter = markdownlint()
    # The run covers every Markdown file in the repo, so its exit status is not this test's to
    # claim. That it never names this file is.
    assert path.name not in linter.stdout + linter.stderr


def test_the_same_quotation_elsewhere_is_still_formatted(
    quotation: Callable[[str], Path],
) -> None:
    """The exemption is `docs/research/` and not `*.md`."""
    path = quotation("tests")

    formatter = fmt_check("tests")

    assert formatter.returncode == 1
    assert path.name in formatter.stdout
