#!/usr/bin/env python3
"""Render the template for all three shapes and prove each result is green.

    pixi run dogfood
    python scripts/dogfood.py --rung no-package --keep

TEMPLATE-ONLY. `scripts/init_repo.py` deletes this file, and the `dogfood` job in `ci.yml`
goes with it, because a derived repo has nothing to render.

The only seam that sees `init-repo` at all, since everything that skill does happens to a
*copy*. For each rung: build a scratch repo, run `scripts/init_repo.py` with that rung's four
answers, then hand the result to its OWN gates — `pixi run check` and the docs build — and
assert what they cannot see. The rendered repo's own gate is the assertion, so almost nothing is
asserted here.

That gate runs over THIS repo's content, which is thin on purpose — research notes that quote
nothing, a glossary of one term, a package of one function. So a green rung proves that
`init-repo` subtracted correctly, and never that a gate behaves correctly on real material. No
fixture corpus is coming: how a rule treats real material is a question for a test that writes
the material it needs and removes it, the way `tests/test_quoted_evidence.py` does. See
`docs/adr/0002-what-the-gate-does-not-check.md`.

Two things about the scratch repo are deliberate:

- It is **re-initialized, not cloned.** GitHub's template button creates a repo with one commit
  holding the whole tree, and `init-repo`'s untouched check compares against that first commit.
  A clone of this repo carries this repo's history instead, under which every file added after
  the initial scaffold reads as modified — so a plain clone would make a fresh repo ask before
  every deletion, which is the opposite of what a template-created repo does.
- It is built from the **working tree**, not from `HEAD`, so this runs against the change you
  are about to commit. In CI the checkout is clean and the two are the same thing.

`pixi install --locked` runs before the gate on purpose. Without it a lock that no longer
matches the manifest is re-solved in silence, and the `no-package` shape — which has to remove
this repo's own editable install from `pixi.lock` — would pass while shipping a lock that says
something else.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The placeholder, spelled in two pieces for the reason `scripts/conformance.py` spells it that
#: way: this file greps a rendered repo for it, and a literal would be a copy for that grep to
#: find. `init-repo` deletes this file, but the two-piece spelling keeps the template's own
#: `check` honest while it is still here.
PLACEHOLDER = "new" + "pkg"

#: The command line's framework. A rung that declined a command line must not name it anywhere,
#: and only a grep sees the whole of that: two tables in `pyproject.toml` and the lock that pins
#: them, each edited by a different anchor in `scripts/init_repo.py`.
CLI_DEPENDENCY = "typer"

#: What this repo says about ITSELF: its own records — how it chose its docs site, what its gate
#: deliberately does not check, and the evidence behind the rules it ships — the two files that
#: publish it as the template, the page describing it and the config that names the site after it,
#: and the two test files that exercise its own configuration. Spelled out here rather than imported
#: from `scripts/init_repo.py`, for the reason the two constants above are: an assertion that reads
#: its expectation out of the thing it is checking cannot tell a deletion that ran from a list that
#: quietly lost an entry.
TEMPLATE_ARTIFACTS = (
    "docs/adr/0001-docs-site-on-zensical.md",
    "docs/adr/0002-what-the-gate-does-not-check.md",
    "docs/research/github-template-mechanics-2026-08-30.md",
    "docs/research/vale-setup-2026-08-30.md",
    "docs/research/zensical-viability-2026-08-30.md",
    "docs/template/index.md",
    "mkdocs.template.yml",
    "tests/test_conformance.py",
    "tests/test_quoted_evidence.py",
)

#: The two repo-settings commands `init-repo`'s closing checklist hands over, and the `ci.yml`
#: jobs each shape keeps. Spelled out here rather than imported from `scripts/init_repo.py`, for
#: the reason the constants above are: a check that reads its expectation out of the thing it is
#: checking cannot tell a shape-correct list from one that drifted off the shapes.
LABEL_COMMAND = "gh label clone liuhlab/liulab-repo-template --force --repo"
SHAPE_CHECKS = {
    "published": ["check", "test", "build", "docs"],
    "not-published": ["check", "test", "build", "docs"],
    "no-package": ["check", "docs"],
}

#: The `PIXI_*` variables a parent `pixi run` exports. They name the TEMPLATE's manifest, and a
#: nested pixi that inherited them would check this repo instead of the rendered one.
PIXI_VARS = tuple(name for name in os.environ if name.startswith("PIXI_"))


@dataclass(frozen=True)
class Rung:
    """One shape, its four interview answers, and what the rendered repo must look like."""

    shape: str
    repo: str
    description: str
    module: str | None = None
    cli: bool = False

    @property
    def answers(self) -> list[str]:
        """The interview, as `scripts/init_repo.py` takes it."""
        args = ["--shape", self.shape, "--description", self.description]
        if self.module is not None:
            args += ["--module", self.module, "--cli" if self.cli else "--no-cli"]
        return args


RUNGS: tuple[Rung, ...] = (
    Rung(
        shape="published",
        repo="liulab-widget",
        module="widget",
        cli=True,
        description="A package the lab publishes, with a command line.",
    ),
    Rung(
        shape="not-published",
        repo="liulab-pipeline",
        module="pipeline",
        cli=False,
        description="A package the lab keeps to itself.",
    ),
    Rung(
        shape="no-package",
        repo="liulab-notes",
        description="Analysis notes and a docs site, with no package.",
    ),
)


def run(args: Sequence[str], cwd: Path, *, label: str) -> str:
    """Run a command in a scratch repo, showing its output only when it fails.

    Returns what it printed. Every caller but one discards that; `init_repo.py`'s is the closing
    checklist, which is the only place the two repo-settings commands exist.
    """
    env = {k: v for k, v in os.environ.items() if k not in PIXI_VARS}
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise RenderError(f"{label} failed (exit {proc.returncode})")
    return proc.stdout


class RenderError(Exception):
    """One rung did not render, or the repo it rendered was not what it claimed."""


def build_scratch_repo(rung: Rung, parent: Path) -> Path:
    """Copy the working tree into a repo with one commit and a remote, as the button makes."""
    stage = parent / rung.repo
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for rel in (p for p in tracked.split("\0") if p):
        source = ROOT / rel
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    git = ["git", "-C", str(stage), "-c", "user.name=dogfood", "-c", "user.email=dogfood@localhost"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "Initial commit"], check=True)
    subprocess.run(
        [*git, "remote", "add", "origin", f"https://github.com/liuhlab/{rung.repo}.git"],
        check=True,
    )
    return stage


def tracked_paths(stage: Path) -> list[str]:
    """Every path the rendered repo tracks."""
    proc = subprocess.run(
        ["git", "-C", str(stage), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in proc.stdout.split("\0") if p]


def check_placeholder(stage: Path) -> list[str]:
    """Grep the rendered repo: no tracked path, link target or file may name the placeholder."""
    problems: list[str] = []
    tracked = tracked_paths(stage)
    # A grep over nothing passes. `git ls-files` reads the index, so a render that deleted
    # without staging, or a repo with no commit, would leave this rule looking green.
    for witness in ("pyproject.toml", "AGENTS.md"):
        if witness not in tracked:
            problems.append(f"{witness} is not tracked, so this grep read almost nothing")
    for rel in tracked:
        path = stage / rel
        if PLACEHOLDER in rel:
            problems.append(f"{rel} still names the placeholder in its path")
        elif path.is_symlink():
            if PLACEHOLDER in str(path.readlink()):
                problems.append(f"{rel} still names the placeholder in its link target")
        elif path.is_file() and PLACEHOLDER.encode() in path.read_bytes():
            problems.append(f"{rel} still names the placeholder in its contents")
    return problems


def check_declined_cli(rung: Rung, stage: Path) -> list[str]:
    """Grep a rung that ships no command line: nothing tracked may still name the framework.

    Deleting `cli.py` and `[project.scripts]` is not enough. A repo that declined a command line
    and still installs the library only that command line imported is carrying residue, and the
    rendered repo's own gate cannot see it — `pixi install --locked` is happy either way.
    """
    if rung.cli:
        return []
    return [
        f"{rel} still names {CLI_DEPENDENCY}, which only the command line used"
        for rel in tracked_paths(stage)
        if not (stage / rel).is_symlink()
        and (stage / rel).is_file()
        and CLI_DEPENDENCY.encode() in (stage / rel).read_bytes()
    ]


def check_template_artifacts(stage: Path) -> list[str]:
    """Grep a rendered repo for what the template says about itself, as a path and a citation.

    None of it is about the repo that inherited it, and the rendered repo's own gate is happy
    either way — a stale ADR passes every rule the template propagates. Citations are checked
    with the paths because a comment pointing at a document that is not there is the same residue
    as the document, spread over more files, and because the citation is the only thing that
    catches a docs task still built with `-f mkdocs.template.yml`.
    """
    tracked = tracked_paths(stage)
    problems = [
        f"{artifact} is the template's own and is still tracked"
        for artifact in TEMPLATE_ARTIFACTS
        if artifact in tracked
    ]
    for rel in tracked:
        path = stage / rel
        if path.is_symlink() or not path.is_file():
            continue
        body = path.read_bytes()
        problems += [
            f"{rel} still points at {artifact}, which this repo does not have"
            for artifact in TEMPLATE_ARTIFACTS
            if artifact.encode() in body
        ]
    return problems


def check_changelog(stage: Path) -> list[str]:
    """Read the rendered changelog: it keeps its preamble and none of the template's entries.

    The file itself is never subtracted — conformance rule 8 wants one wherever `release.yml`
    is — so what is asserted is its CONTENTS. Every Keep a Changelog entry is a list item, so
    asking whether any list item survived tests the shape rather than the template's current
    wording, and an entry added to this repo tomorrow cannot make this pass on nothing.
    """
    text = (stage / "CHANGELOG.md").read_text(encoding="utf-8")
    return [
        f"CHANGELOG.md still carries a template entry: {line.strip()}"
        for line in text.splitlines()
        if line.lstrip().startswith(("- ", "* ", "+ "))
    ]


def check_next_steps(rung: Rung, output: str) -> list[str]:
    """Read the checklist `init_repo.py` printed: both repo-settings commands, filled in.

    Neither one is in the tree, so no other check here can see them — the labels and the ruleset
    are GitHub state, and the checklist is the part of that this repo owns. The required checks
    are read back out of the command rather than matched as one literal string, because what
    can drift is the LIST: `no-package` deletes the `test` and `build` jobs, and a ruleset
    naming a job that never reports makes every pull request in that repo unmergeable.
    """
    problems: list[str] = []
    slug = f"liuhlab/{rung.repo}"
    if f"{LABEL_COMMAND} {slug}" not in output:
        problems.append(f"the checklist does not hand over `{LABEL_COMMAND} {slug}`")
    if f"repos/{slug}/rulesets" not in output:
        problems.append(f"the checklist does not hand over a ruleset command for {slug}")
    contexts = re.findall(r'"context":\s*"([^"]+)"', output)
    if contexts != SHAPE_CHECKS[rung.shape]:
        problems.append(
            f"the ruleset command requires {contexts or 'nothing'}, "
            f"and this shape has {SHAPE_CHECKS[rung.shape]}"
        )
    return problems


def check_shape(rung: Rung, stage: Path) -> list[str]:
    """Check what the rendered repo's own gate cannot see: which files are and are not there."""
    packaged = rung.shape != "no-package"
    expected = {
        # Both symlink directories go once their last skill does, because git drops a directory
        # with nothing tracked in it. The skills LANE stays: `skills/install.py` is kept
        # unconditionally, so a repo that later wants a skill does not reinvent the convention.
        ".claude/skills": False,
        ".agents/skills": False,
        "skills/init-repo": False,
        "skills/template-dev": False,
        "skills/install.py": True,
        # Template machinery deletes itself, including the two scripts behind this job.
        "scripts/init_repo.py": False,
        "scripts/dogfood.py": False,
        "scripts/conformance.py": True,
        # The rungs themselves.
        ".github/workflows/release.yml": rung.shape == "published",
        "src": packaged,
        "tests": packaged,
        "docs/api.md": packaged,
        # Never subtracted, at any rung. `check_changelog` asks the other half of that: the
        # file ships, and the entries under its heading are this repo's or nobody's. The
        # licence is asked the same two questions — it ships, and the year below is this
        # repo's rather than the template's.
        "CHANGELOG.md": True,
        "AGENTS.md": True,
        "GLOSSARY.md": True,
        "LICENSE": True,
        # The tracker, labels and domain layout come from the lab defaults on the machine. A
        # repo's own page replaces that one default for good, so a seeded copy would pin every
        # new repo to the template's wording. Writing rules have no lab default.
        "docs/agents/issue-tracker.md": False,
        "docs/agents/triage-labels.md": False,
        "docs/agents/domain.md": False,
        "docs/agents/writing.md": True,
    }
    problems = [
        f"{rel} is {'missing' if wanted else 'still here'}"
        for rel, wanted in expected.items()
        if (stage / rel).exists() != wanted
    ]
    # The year `init-repo` ran, read from the clock and not from `scripts/init_repo.py`. It
    # bites the year after the template's own file was written, which is exactly when a stamp
    # that stopped running would start shipping a stale licence.
    licence = stage / "LICENSE"
    copyright_line = f"Copyright (c) {date.today().year} Liu Lab"
    if licence.is_file() and copyright_line not in licence.read_text(encoding="utf-8"):
        problems.append(f"LICENSE does not say {copyright_line!r}, the year this render ran")
    workflow = (stage / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "dogfood" in workflow:
        problems.append("ci.yml still carries the dogfood job")
    if packaged and not (stage / f"src/{rung.module}/__init__.py").is_file():
        problems.append(f"src/{rung.module}/ is not the renamed package")
    return problems


def render(rung: Rung, parent: Path) -> None:
    """Render one rung and put the result through its own gate."""
    stage = build_scratch_repo(rung, parent)
    checklist = run(
        [sys.executable, str(stage / "scripts" / "init_repo.py"), *rung.answers],
        stage,
        label="init_repo.py",
    )
    manifest = ["--manifest-path", str(stage / "pyproject.toml")]
    run(["pixi", "install", "--locked", *manifest], stage, label="pixi install --locked")
    run(["pixi", "run", "--locked", *manifest, "check"], stage, label="pixi run check")
    # The docs build is a job of its own in the rendered repo, and it is the only thing that
    # reads `mkdocs.yml` and `docs/api.md` — both of which `init-repo` edits. `--strict` is in
    # the task, so a reference to a module this rung deleted fails here.
    run(["pixi", "run", "--locked", *manifest, "docs-build"], stage, label="pixi run docs-build")
    problems = (
        check_placeholder(stage)
        + check_shape(rung, stage)
        + check_declined_cli(rung, stage)
        + check_template_artifacts(stage)
        + check_changelog(stage)
        + check_next_steps(rung, checklist)
    )
    if problems:
        raise RenderError("\n".join(f"    {problem}" for problem in problems))


def main(argv: Sequence[str] | None = None) -> int:
    """Render every rung, and report all of them rather than stopping at the first failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rung", choices=[r.shape for r in RUNGS], help="Render only this shape.")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch repos afterwards.")
    args = parser.parse_args(argv)
    rungs = [r for r in RUNGS if args.rung in (None, r.shape)]

    parent = Path(tempfile.mkdtemp(prefix="liulab-dogfood."))
    print(f"dogfood — rendering {len(rungs)} shape(s) in {parent}\n")
    failures: list[tuple[Rung, str]] = []
    for rung in rungs:
        started = time.monotonic()
        print(f"  {rung.shape:<14} {rung.repo} ...", flush=True)
        try:
            render(rung, parent)
        except RenderError as failure:
            failures.append((rung, str(failure)))
            print(f"  {rung.shape:<14} FAILED after {time.monotonic() - started:.0f}s")
        else:
            print(f"  {rung.shape:<14} green in {time.monotonic() - started:.0f}s")

    if not failures:
        if not args.keep:
            shutil.rmtree(parent, ignore_errors=True)
        print(f"\nAll {len(rungs)} rendered repo(s) pass their own gate.")
        return 0
    print(f"\ndogfood: {len(failures)} of {len(rungs)} rung(s) failed.\n", file=sys.stderr)
    for rung, detail in failures:
        print(f"  {rung.shape}\n{detail}\n", file=sys.stderr)
    print(f"  the scratch repos are in {parent}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
