#!/usr/bin/env python3
r"""The mechanical half of `init-repo`: everything the interview does not have to judge.

    python scripts/init_repo.py --shape published --module widget --cli \\
        --description "What this repo is for."
    python scripts/init_repo.py --shape no-package --description "..." --plan

`skills/init-repo/SKILL.md` conducts the interview — four questions, all judgement. This
script performs every action those answers imply, deterministically and without an agent, so
the `dogfood` job can render the template for all three shapes on every pull request and prove
each result is green.

Actions come in two kinds, and only one of them is dangerous:

- **Substitutions always run.** Renaming the placeholder package directory is right whether it
  holds one file or fifty, so a repo initialized eight months late still gets its rename.
- **A deletion or an overwrite runs unprompted only when its target is byte-identical to the
  first commit.** A template-created repo starts with one commit, so
  `git diff --quiet <first commit> -- <path>` settles that exactly rather than heuristically.
  Otherwise it asks, and the default is keep. `--plan` prints the same survey and changes
  nothing; `--force <path>` acts on a changed target anyway, for a caller that has already
  asked the person.

Exempt from that check, deleted unconditionally and tolerated when absent: the two skill
directories, their four symlinks, the `dogfood` job and its task, `scripts/dogfood.py`, this
file, the two files that publish this repo as the template — `docs/template/index.md` and the
`mkdocs.template.yml` that names the site after it — and the template's own records, the two ADRs
and the three research notes that say how the template was built and what its gate deliberately
leaves alone. Their removal is the entire point, so nothing about them is negotiable.
`CHANGELOG.md` is the one record handled the other way: the file always ships, and emptying it
of the template's entries is guarded, so a late init never discards what someone wrote.

Everything else is an ANCHORED edit: it matches text the template ships and reports a miss
rather than failing. A repo that rewrote the passage keeps what it wrote, and the one thing
that must not survive — the placeholder name — is checked by conformance rule 1 in the
rendered repo, so a missed anchor turns the dogfood job red instead of shipping quietly.

The `no-package` shape prunes more than the spec's `src/` and `tests/`, because those two
deletions alone do not leave a green repo: pixi cannot install a package with no source,
pytest exits 5 on a `tests/` that is not there, and `zensical build --strict` fails on four
mkdocstrings references to a module that is gone. `prune_package` is that list, and every
entry is there because something went red without it.

Limitation, recorded on purpose: a repo seeded by clone-and-push, or whose history was
squashed, reads as fully modified, so it errs toward asking rather than deleting.
"""

from __future__ import annotations

import argparse
import json
import keyword
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

#: The template's placeholder identity, SPELLED IN TWO PIECES for the reason
#: `scripts/conformance.py` spells it that way, and one more: a person who renames the tree by
#: hand before finding this skill must not rewrite the thing that performs renames. A literal
#: here would turn these constants into the new module's own name and the script into a no-op.
PLACEHOLDER_MODULE = "new" + "pkg"
PLACEHOLDER_DIST = f"liulab-{PLACEHOLDER_MODULE}"
PLACEHOLDER_DESCRIPTION = "One line: what this repo is for. `init-repo` rewrites this."

#: Three rungs on one ladder. `published` subtracts nothing, `not-published` drops the release
#: workflow, `no-package` also drops `src/` and `tests/`. The `CHANGELOG.md` FILE survives all
#: three; its entries do not, which is a different question and `CHANGELOG_UNRELEASED` answers it.
SHAPES = ("published", "not-published", "no-package")

#: Used when a remote is a local path rather than a GitHub URL, which is what a scratch clone
#: has. The lab account is the right guess and the site URL says so out loud.
DEFAULT_OWNER = "liuhlab"

#: The lab's label set, defined here and nowhere else — the wayfinder skill uses the
#: `wayfinder:*` labels but declares no colour or description for any of them. GitHub copies no
#: label when a repo is created from a template, so a new repo has the nine stock ones until
#: someone clones these.
LABEL_SOURCE = "liuhlab/liulab-repo-template"

#: The `ci.yml` jobs each shape actually keeps. `no-package` deletes `test` and `build`, so a
#: ruleset naming those would wait forever for a check that never reports, and every pull
#: request in that repo would be unmergeable.
SHAPE_CHECKS = {
    "published": ("check", "test", "build", "docs"),
    "not-published": ("check", "test", "build", "docs"),
    "no-package": ("check", "docs"),
}

#: Deleted unconditionally, tolerated when absent, never checked against the first commit. This
#: script is last, so everything else has already happened by the time it goes.
TEMPLATE_ONLY = (
    "skills/init-repo",
    "skills/template-dev",
    ".claude/skills/init-repo",
    ".claude/skills/template-dev",
    ".agents/skills/init-repo",
    ".agents/skills/template-dev",
    # The template's own site: the page about the template, and the config that names the site
    # after it. `mkdocs.yml` is the config every repo keeps and is not here.
    "docs/template/index.md",
    "mkdocs.template.yml",
    "scripts/dogfood.py",
    "scripts/init_repo.py",
    # Tests of the template's own configuration, not of the new repo's code.
    "tests/test_conformance.py",
    "tests/test_quoted_evidence.py",
)

#: The template's own records: the two decisions it made about its own gate and docs site, and
#: the three notes behind the rules it ships. Same category as the two skills above — machinery
#: whose removal is the point — so they go the same way, unconditionally and tolerated when
#: absent. A record whose subject is THIS repo is residue in a repo made from it, whatever it
#: happens to number: `0002` reasons about `scripts/dogfood.py`, which the same render deletes.
#:
#: BY EXACT PATH, never by directory or glob. A repo initialized late may already have written
#: `docs/adr/0003-*.md` or a research note of its own, and deleting `docs/adr/` would take it
#: with these. Both directories are allowed to disappear with their last file: `Repo.delete`
#: prunes what it empties, and the lab's domain-docs default says `/domain-modeling` creates
#: `docs/adr/` when it needs one and that a missing one is not an error. No `.gitkeep` — a
#: tracked file whose only job is to exist is exactly the residue this script removes.
#:
#: Only `TEMPLATE_ADR` and `TEMPLATE_VALE_NOTE` are named, because only those two are cited
#: again in `RECORD_POINTERS`. A record nothing else names stays a literal here.
TEMPLATE_ADR = "docs/adr/0001-docs-site-on-zensical.md"
TEMPLATE_VALE_NOTE = "docs/research/vale-setup-2026-08-30.md"
TEMPLATE_RECORDS = (
    TEMPLATE_ADR,
    "docs/adr/0002-what-the-gate-does-not-check.md",
    "docs/research/github-template-mechanics-2026-08-30.md",
    TEMPLATE_VALE_NOTE,
    "docs/research/zensical-viability-2026-08-30.md",
)

#: Every citation of a record above that lives in a file a derived repo KEEPS, and what the
#: sentence becomes once the citation goes. Deleting a document and leaving five files pointing
#: at it trades one kind of residue for a worse one, so each citation is cut where it stands
#: rather than the paragraph explaining the rule around it.
RECORD_POINTERS = (
    ("mkdocs.yml", f"# See {TEMPLATE_ADR}.\n", ""),
    (".github/workflows/docs.yml", f" See `{TEMPLATE_ADR}`.", ""),
    ("styles/Lab/Readability.yml", f" See `{TEMPLATE_VALE_NOTE}`.", ""),
    (
        "styles/Lab/Jargon.yml",
        f"# The measurements and the per-pattern evidence are in\n# `{TEMPLATE_VALE_NOTE}`. Do not",
        "# Do not",
    ),
)

#: The heading the changelog is reset to. Everything below it is the TEMPLATE's history — one
#: entry even describes the release workflow the lower two rungs delete — and none of it is the
#: new repo's. Truncating at the heading keeps the preamble, which is the part that says what
#: format to write in, and keeps this file from holding a second copy of it to drift from.
CHANGELOG_UNRELEASED = "## [Unreleased]"

#: Files carrying an `init-repo:begin <name>` / `init-repo:end <name>` pair. Template-only text
#: inside a file every repo keeps cannot be deleted by path — the dogfood job is one job in a
#: workflow, the front page's pointer at `docs/template/` is one paragraph of a page — so each
#: ships delimited, and removing it stays mechanical instead of becoming YAML or prose surgery.
MARKED_BLOCKS = (
    (".github/workflows/ci.yml", "dogfood"),
    ("pyproject.toml", "dogfood"),
    ("docs/index.md", "template-docs"),
)

#: The task lines a repo with no package has no use for, matched whole.
TASKS_A_PACKAGE_NEEDS = frozenset({'test = "pytest"', 'build = "python -m build"'})

#: The command line's framework, and the two lines `pyproject.toml` declares it on: the wheel's
#: own requirement and the conda mirror pixi installs from. A repo that declined a command line
#: must not install the library only its command line imported, so both go — and
#: `drop_lock_dependency` takes the package out of `pixi.lock` with them. Both anchors are the
#: declarations themselves, never the prose near them, so rewording a comment cannot break this.
CLI_DEPENDENCY = "typer"
CLI_PROJECT_DEPENDENCY = f'dependencies = ["{CLI_DEPENDENCY}>=0.12"]'
CLI_PIXI_DEPENDENCY = f'{CLI_DEPENDENCY} = ">=0.12"'

#: The licence's copyright line. The pattern matches whatever year the template shipped, and the
#: rendered repo gets the year this ran — a year written once is a fact that goes stale and
#: nothing rechecks it. The text itself is the MIT licence every other lab repo carries.
LICENSE_COPYRIGHT = r"(?m)^Copyright \(c\) \d{4} Liu Lab$"

#: Committing and tagging need a name and an email, and a fresh runner has neither.
#: `Repo.fallback_identity` uses this only when git lacks one, since `-c` beats the user's own.
GIT_IDENTITY = ("-c", "user.name=init-repo", "-c", "user.email=init-repo@localhost")


# --------------------------------------------------------------------------------------
# Text surgery. Every one of these returns `None` when its anchor is not in the text, so the
# caller reports a miss rather than writing back a file it did not understand.
# --------------------------------------------------------------------------------------


def _lines(text: str) -> list[str]:
    """Split into lines, keeping the endings so a join round-trips exactly."""
    return text.splitlines(keepends=True)


def _cut(lines: list[str], start: int, end: int) -> str:
    """Remove `lines[start:end]`, and one blank line if the cut would leave two."""
    rest = lines[:start] + lines[end:]
    if start > 0 and start < len(rest) and not rest[start - 1].strip() and not rest[start].strip():
        del rest[start]
    return "".join(rest)


def _find(lines: list[str], accepts: Callable[[str], bool]) -> int | None:
    """Return the index of the first line the predicate accepts."""
    return next((i for i, line in enumerate(lines) if accepts(line)), None)


def drop_lines(text: str, accepts: Callable[[str], bool]) -> str | None:
    """Remove every line the predicate accepts."""
    lines = _lines(text)
    kept = [line for line in lines if not accepts(line)]
    return "".join(kept) if len(kept) != len(lines) else None


def drop_example(text: str, prefix: str) -> str | None:
    """Remove a sentence and the fenced code block under it."""
    lines = _lines(text)
    start = _find(lines, lambda line: line.startswith(prefix))
    if start is None:
        return None
    fences = 0
    end = start
    while end < len(lines):
        if lines[end].startswith("```"):
            fences += 1
            if fences == 2:
                end += 1
                break
        end += 1
    else:
        return None
    while end < len(lines) and not lines[end].strip():
        end += 1
    return _cut(lines, start, end)


def drop_comment_block(text: str, prefix: str) -> str | None:
    """Remove a run of `#` comment lines, from this prefix down to the last one below it.

    A bare `#` immediately above is taken too: that separator belongs to the block being
    removed, not to the paragraph above it.
    """
    lines = _lines(text)
    start = _find(lines, lambda line: line.startswith(prefix))
    if start is None:
        return None
    while start > 0 and lines[start - 1].rstrip() == "#":
        start -= 1
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("#"):
        end += 1
    return _cut(lines, start, end)


def truncate_at(text: str, heading: str) -> str | None:
    """Keep everything down to and including this heading, and drop what is under it."""
    lines = _lines(text)
    start = _find(lines, lambda line: line.rstrip() == heading)
    if start is None:
        return None
    return "".join(lines[: start + 1])


def drop_marked_block(text: str, name: str) -> str | None:
    """Remove everything from `init-repo:begin <name>` through `init-repo:end <name>`."""
    lines = _lines(text)
    start = _find(lines, lambda line: f"init-repo:begin {name}" in line)
    end = _find(lines, lambda line: f"init-repo:end {name}" in line)
    if start is None or end is None or end < start:
        return None
    return _cut(lines, start, end + 1)


def drop_toml_table(text: str, header: str) -> str | None:
    """Remove one TOML table: the comment banner above it, the header, and its entries.

    The table ends where the next one begins, less the blank lines and comments directly above
    that header — those introduce the next table rather than closing this one.
    """
    lines = _lines(text)
    start = _find(lines, lambda line: line.rstrip() == header)
    if start is None:
        return None
    head = start
    while head > 0 and (lines[head - 1].startswith("#") or not lines[head - 1].strip()):
        head -= 1
    while head < start and not lines[head].strip():
        head += 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    while end > start + 1 and (lines[end - 1].startswith("#") or not lines[end - 1].strip()):
        end -= 1
    return _cut(lines, head, end)


def drop_markdown_section(text: str, heading: str) -> str | None:
    """Remove one section: its heading down to the next heading at the same level or above.

    Fenced code is skipped, so a shell comment inside an example cannot be read as a heading.
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = _lines(text)
    start = _find(lines, lambda line: line.rstrip() == heading)
    if start is None:
        return None
    end = len(lines)
    fenced = False
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("```"):
            fenced = not fenced
            continue
        if fenced or not lines[i].startswith("#"):
            continue
        body = lines[i].lstrip("#")
        if body.startswith(" ") and len(lines[i]) - len(body) <= level:
            end = i
            break
    return _cut(lines, start, end)


def drop_indented_block(text: str, prefix: str) -> str | None:
    """Remove a YAML key and every line indented deeper than it."""
    lines = _lines(text)
    start = _find(lines, lambda line: line.startswith(prefix))
    if start is None:
        return None
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return _cut(lines, start, end)


def drop_python_function(text: str, name: str) -> str | None:
    """Remove one top-level function, from its `def` to the next top-level statement."""
    lines = _lines(text)
    start = _find(lines, lambda line: line.startswith(f"def {name}("))
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][0].isspace():
            end = i
            break
    return _cut(lines, start, end)


def _imported_names(line: str) -> list[str]:
    """Return the names one import statement binds, or nothing when this is not an import."""
    if line.startswith("from __future__"):
        return []
    match = re.match(r"^(?:from\s+[\w.]+\s+)?import\s+(.+?)\s*$", line)
    if match is None:
        return []
    return [part.strip().split(" as ")[-1].strip() for part in match[1].split(",")]


def drop_unused_imports(text: str) -> str | None:
    """Remove import lines whose names appear nowhere else in the file.

    Deleting a test takes its imports with it. Naming those imports here would hard-code which
    one belongs to which test; asking whether the name is still used cannot go stale.
    """
    lines = _lines(text)
    keep = [True] * len(lines)
    for i, line in enumerate(lines):
        names = _imported_names(line)
        if not names:
            continue
        body = "".join(other for j, other in enumerate(lines) if j != i and keep[j])
        if any(re.search(rf"\b{re.escape(name)}\b", body) for name in names):
            continue
        keep[i] = False
        # The blank line below goes too, but only when the line above is already blank —
        # otherwise this eats the separator the formatter wants before the next block.
        if i > 0 and not lines[i - 1].strip() and i + 1 < len(lines) and not lines[i + 1].strip():
            keep[i + 1] = False
    if all(keep):
        return None
    return "".join(line for i, line in enumerate(lines) if keep[i])


def drop_lock_self_reference(text: str) -> str | None:
    """Remove this repo's own editable install from `pixi.lock`.

    A repo with no `src/` cannot install itself, so the manifest loses that dependency and the
    lock has to lose it too: otherwise `pixi install --locked` refuses, and a plain
    `pixi install` quietly re-solves against whatever the index holds today. Two shapes to
    remove — one reference inside each environment, and the package entry at the end.
    """
    lines = _lines(text)
    kept: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if line.strip() == "- pypi: ./" and line.startswith(" "):
            index += 1
            changed = True
            continue
        if line.rstrip() == "- pypi: ./":
            index += 1
            while index < len(lines) and lines[index].startswith("  "):
                index += 1
            changed = True
            continue
        kept.append(line)
        index += 1
    return "".join(kept) if changed else None


def drop_cli_dependency(text: str) -> str | None:
    """Remove the command line's framework from both of `pyproject.toml`'s dependency tables.

    Declared twice on purpose — the wheel's own requirement, and the conda mirror pixi installs
    from — so it has to be taken out twice. `[project] dependencies` is emptied rather than
    deleted: the key is where the first real dependency goes, and a wheel with no dependencies
    says so explicitly.
    """
    anchors: tuple[Callable[[str], str | None], ...] = (
        lambda t: _replace(t, CLI_PROJECT_DEPENDENCY, "dependencies = []"),
        lambda t: drop_lines(t, lambda line: line.rstrip() == CLI_PIXI_DEPENDENCY),
    )
    edited = text
    for anchor in anchors:
        found = anchor(edited)
        if found is None:
            return None
        edited = found
    return edited


def _lock_package_name(url: str) -> str:
    """Read a conda package's name off its URL: the file name, less the version and the build."""
    return url.rsplit("/", 1)[-1].rsplit("-", 2)[0]


def _lock_pool(lines: list[str]) -> dict[str, tuple[str, set[str]]]:
    """Map every conda package in the lock's pool to its name and the names it depends on.

    The pool is the flat list at the end of the file — one entry per package, starting at column
    zero, with its `depends:` block indented under it.
    """
    pool: dict[str, tuple[str, set[str]]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.startswith("- conda: "):
            continue
        url = line.removeprefix("- conda: ").strip()
        depends: set[str] = set()
        listing = False
        while index < len(lines) and lines[index].startswith(" "):
            entry = lines[index].rstrip()
            index += 1
            if entry == "  depends:":
                listing = True
            elif not entry.startswith("  - "):
                listing = False
            elif listing:
                depends.add(entry.removeprefix("  - ").split()[0])
        pool[url] = (_lock_package_name(url), depends)
    return pool


def _dropped_references(
    lines: list[str], span: range, pool: dict[str, tuple[str, set[str]]], name: str
) -> set[int]:
    """Pick the lines of one environment's package list that go when `name` does.

    `name` itself, then anything it reaches that nothing else in this environment still needs —
    repeatedly, because a dependency of a dependency is an orphan too.
    """
    at: dict[str, int] = {}
    needs: dict[str, set[str]] = {}
    for index in span:
        entry = pool.get(lines[index].strip().removeprefix("- conda: "))
        if entry is None:
            return set()
        at[entry[0]], needs[entry[0]] = index, entry[1]
    if name not in at:
        return set()
    reachable: set[str] = set()
    stack = [name]
    while stack:
        for dependency in needs[stack.pop()]:
            if dependency in needs and dependency not in reachable:
                reachable.add(dependency)
                stack.append(dependency)
    doomed = {name}
    while True:
        orphans = {
            package
            for package in reachable - doomed
            if not any(package in needs[other] for other in needs.keys() - doomed)
        }
        if not orphans:
            return {at[package] for package in doomed}
        doomed |= orphans


def _is_lock_reference(line: str) -> bool:
    """Whether this line lists a conda package in an environment, rather than defining one."""
    return line.startswith(" ") and line.lstrip().startswith("- conda: ")


def _drop_requirement(lines: list[str], name: str) -> list[str]:
    """Remove one requirement from every `requires_dist` in the lock, and a key it empties."""
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.rstrip() != "  requires_dist:":
            kept.append(line)
            continue
        requirements = []
        while index < len(lines) and lines[index].startswith("  - "):
            if re.match(rf"  - {re.escape(name)}(?![\w.-])", lines[index]) is None:
                requirements.append(lines[index])
            index += 1
        if requirements:
            kept += [line, *requirements]
    return kept


def drop_lock_dependency(text: str, name: str) -> str | None:
    """Remove one conda package from `pixi.lock`, along with whatever came in only for it.

    A dependency taken out of `pyproject.toml` has to come out of here too: `pixi install
    --locked` refuses a lock holding packages the manifest no longer asks for, and re-solving is
    not an option — it needs the network, and a rendered repo installs from the lock it
    inherited. What came in with the package is FOUND rather than listed, the way
    `drop_unused_imports` asks whether a name is still used: everything the package reaches that
    nothing else in that environment still needs goes with it, so a version bump that changes
    its dependencies cannot leave this stale.
    """
    lines = _lines(text)
    pool = _lock_pool(lines)
    doomed: set[int] = set()
    start: int | None = None
    for index in range(len(lines) + 1):
        if index < len(lines) and _is_lock_reference(lines[index]):
            start = index if start is None else start
        elif start is not None:
            doomed |= _dropped_references(lines, range(start, index), pool, name)
            start = None
    kept = [line for index, line in enumerate(lines) if index not in doomed]

    # The pool is shared by every environment, so an entry goes only once nothing lists it.
    listed = {line.strip().removeprefix("- conda: ") for line in kept if _is_lock_reference(line)}
    pruned: list[str] = []
    index = 0
    while index < len(kept):
        line = kept[index]
        index += 1
        if line.startswith("- conda: ") and line.removeprefix("- conda: ").strip() not in listed:
            while index < len(kept) and kept[index].startswith(" "):
                index += 1
            continue
        pruned.append(line)

    edited = "".join(_drop_requirement(pruned, name))
    return edited if edited != text else None


def _replace(text: str, old: str, new: str) -> str | None:
    """Literal replacement that reports a miss instead of silently doing nothing."""
    return text.replace(old, new) if old in text else None


def _sub(text: str, pattern: str, replacement: str) -> str | None:
    """Regular expression replacement, with the replacement taken literally."""
    edited, count = re.subn(pattern, lambda _match: replacement, text)
    return edited if count else None


# --------------------------------------------------------------------------------------
# The repo
# --------------------------------------------------------------------------------------


def parse_remote(url: str) -> tuple[str, str]:
    """Split a git remote into (owner, repository).

    Handles the forms a template-created repo carries — HTTPS and SSH — and falls back to the
    lab account and the last path element for a local path, which is what a scratch clone has.
    """
    trimmed = url.strip().removesuffix(".git").rstrip("/")
    if "://" in trimmed or "@" in trimmed:
        match = re.search(r"[:/]([^/:]+)/([^/:]+)$", trimmed)
        if match is not None:
            return match[1], match[2]
    return DEFAULT_OWNER, Path(trimmed).name


@dataclass
class Identity:
    """Who the rendered repo says it is. All of it derived, none of it asked."""

    owner: str
    repo: str
    dist: str
    module: str | None
    command: str | None
    description: str
    shape: str

    @property
    def site_url(self) -> str:
        """Where GitHub Pages publishes this repository's site."""
        return f"https://{self.owner}.github.io/{self.repo}/"


class Repo:
    """The tree being rendered, and every git question asked about it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._first_commit: str | None = None

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run one git command in this repo."""
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise SystemExit(f"init-repo: `git {' '.join(args)}` failed\n{detail}")
        return proc

    @property
    def fallback_identity(self) -> tuple[str, ...]:
        """`GIT_IDENTITY` when git has no name or no email here, and nothing when it has both."""
        for key in ("user.name", "user.email"):
            proc = self.git("config", "--get", key, check=False)
            if proc.returncode != 0 or not proc.stdout.strip():
                return GIT_IDENTITY
        return ()

    @property
    def tracked(self) -> list[str]:
        """Every tracked path, as git reports it."""
        return sorted(p for p in self.git("ls-files", "-z").stdout.split("\0") if p)

    def is_tracked(self, rel: str) -> bool:
        """Whether anything tracked lives at or under this path."""
        return bool(self.git("ls-files", "-z", "--", rel).stdout.strip("\0"))

    @property
    def first_commit(self) -> str:
        """The root commit. A template-created repo has exactly one, holding the whole tree."""
        if self._first_commit is None:
            commits = self.git("rev-list", "--max-parents=0", "HEAD").stdout.split()
            if not commits:
                raise SystemExit("init-repo: this repo has no commits, so there is nothing to do")
            self._first_commit = commits[-1]
        return self._first_commit

    def untouched(self, rel: str) -> bool:
        """Whether a path is byte-identical to the first commit."""
        proc = self.git("diff", "--quiet", self.first_commit, "--", rel, check=False)
        return proc.returncode == 0

    def read(self, rel: str) -> str | None:
        """Read a text file, or return `None` when it is absent, a symlink, or binary."""
        path = self.root / rel
        if path.is_symlink() or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

    def write(self, rel: str, text: str) -> None:
        """Write a file, normalized to exactly one trailing newline."""
        (self.root / rel).write_text(text.rstrip("\n") + "\n", encoding="utf-8")

    def delete(self, rel: str) -> bool:
        """Remove a path from the index and the disk, pruning the directories it empties."""
        path = self.root / rel
        if self.is_tracked(rel):
            self.git("rm", "-r", "-q", "--", rel)
        elif path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return False
        parent = path.parent
        while parent != self.root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        return True

    def move(self, source: str, target: str) -> None:
        """Rename a tracked path."""
        (self.root / target).parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", source, target)


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


@dataclass
class Guarded:
    """One deletion or overwrite, and what the untouched check decided about it."""

    path: str
    verb: str
    reason: str
    untouched: bool = True
    approved: bool = True


@dataclass
class Init:
    """One rendering of the template into a repo of its own."""

    repo: Repo
    identity: Identity
    forced: set[str] = field(default_factory=set)
    guarded: list[Guarded] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)

    # -- reporting ---------------------------------------------------------------------

    def say(self, action: str, detail: str) -> None:
        """Print one action line."""
        print(f"  {action:<9} {detail}")

    def note(self, detail: str) -> None:
        """Print a line under the action above it."""
        print(f"            {detail}")

    def edit(self, rel: str, label: str, surgery: Callable[[str], str | None]) -> bool:
        """Apply one anchored edit, and say so when the anchor is not there."""
        text = self.repo.read(rel)
        if text is None:
            self.misses.append(f"{rel} ({label}): the file is not here")
            return False
        edited = surgery(text)
        if edited is None:
            self.misses.append(f"{rel} ({label}): the template's own text is not there any more")
            return False
        self.repo.write(rel, edited)
        self.say("edited", f"{rel} — {label}")
        return True

    def remove(self, rel: str) -> None:
        """Delete a path and say so, quietly tolerating one that is already gone."""
        if self.repo.delete(rel):
            self.say("deleted", rel)

    # -- the guarded set ---------------------------------------------------------------

    def survey(self) -> None:
        """Decide, before anything changes, which deletions and overwrites may run."""
        targets = [
            Guarded("README.md", "rewrite", "it describes the template, not this repo"),
            # The FILE is never subtracted — rule 8 wants one wherever `release.yml` is — but its
            # entries are the template's history. Guarded, and only here: a repo initialized
            # eight months late has real entries under that heading, and they are not ours to cut.
            Guarded("CHANGELOG.md", "reset", "its entries are the template's, not this repo's"),
        ]
        if self.identity.shape != "published":
            targets.append(
                Guarded(".github/workflows/release.yml", "delete", "this repo does not publish")
            )
        if self.identity.shape == "no-package":
            targets += [
                Guarded("src", "delete", "this repo has no package"),
                Guarded("tests", "delete", "this repo has no package"),
            ]
        for target in targets:
            target.untouched = self.repo.untouched(target.path)
            target.approved = target.untouched or target.path in self.forced
        self.guarded = targets

    def confirm(self) -> None:
        """Ask about each changed target. Keep is the default, at a prompt and without one."""
        for target in self.guarded:
            if target.approved:
                continue
            if sys.stdin.isatty():
                answer = input(
                    f"  {target.path} has changed since the first commit. "
                    f"{target.verb.capitalize()} it anyway? [keep/{target.verb}] (keep): "
                )
                target.approved = answer.strip().lower() in {target.verb, target.verb[0]}
                if target.approved:
                    continue
            self.say("kept", f"{target.path} — it has changed since the first commit")
            self.note(f"pass --force {target.path} to {target.verb} it anyway")

    def approved(self, path: str) -> bool:
        """Whether one guarded target may be acted on."""
        return any(target.path == path and target.approved for target in self.guarded)

    # -- the phases --------------------------------------------------------------------

    def remove_template_only(self) -> None:
        """Delete the artifacts whose removal is the point, wherever they still are."""
        for rel in (*TEMPLATE_ONLY, *TEMPLATE_RECORDS):
            self.remove(rel)
        for rel, name in MARKED_BLOCKS:
            self.edit(rel, f"the {name} block", lambda text, n=name: drop_marked_block(text, n))

    def prune_shared(self) -> None:
        """Apply the anchored edits every rendered repo gets, whatever its shape."""
        self.edit(
            "mkdocs.yml",
            "the note about the placeholder identity",
            lambda text: drop_comment_block(text, "# These three carry the PLACEHOLDER"),
        )
        self.edit(
            "GLOSSARY.md",
            "the placeholder glossary entry",
            lambda text: drop_markdown_section(text, "### Placeholder package"),
        )
        self.edit(
            "pyproject.toml",
            "the note about the template's own site config",
            lambda text: drop_comment_block(text, "# `-f mkdocs.template.yml` is TEMPLATE-ONLY"),
        )
        self.edit(
            "pyproject.toml",
            "the template's site config, in both docs tasks",
            lambda text: _replace(
                text,
                'docs = "zensical serve -f mkdocs.template.yml"\n'
                'docs-build = "zensical build -f mkdocs.template.yml --clean --strict"',
                'docs = "zensical serve"\ndocs-build = "zensical build --clean --strict"',
            ),
        )
        for rel, old, new in RECORD_POINTERS:
            self.edit(
                rel,
                "the citation of a record this repo does not keep",
                lambda text, o=old, n=new: _replace(text, o, n),
            )

    def prune_cli_dependency(self) -> None:
        """Take the command line's framework out of the manifest and the lock, together.

        Both shapes that ship no command line come through here — the one that declined it and
        the one that has no package at all — because a repo carrying the library only its
        command line imported is exactly the residue this template exists to prevent.
        """
        self.edit(
            "pyproject.toml",
            f"{CLI_DEPENDENCY}, in both dependency tables",
            drop_cli_dependency,
        )
        self.edit(
            "pixi.lock",
            f"{CLI_DEPENDENCY} and the packages that came with it",
            lambda text: drop_lock_dependency(text, CLI_DEPENDENCY),
        )

    def prune_cli(self) -> None:
        """Take the command line out of a package that does not want one."""
        self.remove(f"src/{PLACEHOLDER_MODULE}/cli.py")
        self.edit(
            "pyproject.toml",
            "[project.scripts]",
            lambda text: drop_toml_table(text, "[project.scripts]"),
        )
        self.prune_cli_dependency()
        self.edit(
            "docs/api.md",
            "the command line reference",
            lambda text: drop_markdown_section(text, "## The command line"),
        )
        self.edit(
            "docs/index.md",
            "the shell example",
            lambda text: drop_example(text, "The same thing from a shell:"),
        )
        self.edit(
            "tests/test_placeholder.py",
            "the command line test",
            lambda text: drop_python_function(text, "test_the_cli_verb_prints_the_greeting"),
        )
        self.edit("tests/test_placeholder.py", "the imports that test used", drop_unused_imports)

    def prune_package(self) -> None:
        """Take out everything that only means something in a repo that ships a package."""
        # Both or neither. Keeping `src/` while deleting `tests/` would fail conformance rule
        # 8 — a package with no test directory — so a repo that keeps the code it wrote keeps
        # the tests with it, and says so.
        if self.approved("src") and self.approved("tests"):
            self.remove("src")
            self.remove("tests")
        else:
            self.say("kept", "src/ and tests/ together — a package with no tests fails rule 8")
        self.remove("docs/api.md")

        for header in (
            "[project.scripts]",
            "[tool.hatch.build.targets.wheel]",
            "[tool.pixi.pypi-dependencies]",
            "[tool.pytest.ini_options]",
            "[tool.ruff.lint.per-file-ignores]",
        ):
            self.edit("pyproject.toml", header, lambda text, h=header: drop_toml_table(text, h))
        self.edit(
            "pyproject.toml",
            "the test and build tasks",
            # Matched WHOLE, not by prefix: `[tool.pixi.environments]` also has a line starting
            # `test = `, and dropping that one takes the environment out of the manifest while
            # the lock file keeps it, which fails `pixi install --locked` on its own.
            lambda text: drop_lines(text, lambda line: line.rstrip() in TASKS_A_PACKAGE_NEEDS),
        )
        self.edit(
            "pyproject.toml",
            "pytest in the check task",
            lambda text: _replace(
                text,
                'check = { depends-on = ["check-static", "test"] }',
                'check = { depends-on = ["check-static"] }',
            ),
        )
        self.edit(
            "pyproject.toml",
            "src and tests in the pyright scope",
            lambda text: _replace(
                text,
                'include = ["src", "tests", "scripts", "skills"]',
                'include = ["scripts", "skills"]',
            ),
        )
        self.prune_cli_dependency()
        self.edit("pixi.lock", "the editable install of this repo", drop_lock_self_reference)
        for job in ("test", "build"):
            self.edit(
                ".github/workflows/ci.yml",
                f"the {job} job",
                lambda text, j=job: drop_indented_block(text, f"  {j}:"),
            )
        self.edit(
            ".github/workflows/ci.yml",
            "the note about the test job",
            lambda text: _replace(
                text,
                "      # `check-static`, not `check`: `check` is the static steps plus pytest, "
                "and pytest is\n      # the `test` job below, in the environment that owns it.\n",
                "      # `check-static` and `check` are the same thing here: this repo has no "
                "tests.\n",
            ),
        )
        self.edit(
            "mkdocs.yml",
            "the API reference in the nav",
            lambda text: drop_lines(text, lambda line: line.strip() == "- API reference: api.md"),
        )
        self.edit(
            "mkdocs.yml",
            "the mkdocstrings plugin",
            lambda text: drop_indented_block(text, "  - mkdocstrings:"),
        )
        self.edit(
            "AGENTS.md",
            "the package names",
            lambda text: _replace(
                text,
                f" Distribution name **`{PLACEHOLDER_DIST}`**, "
                f"import name **`{PLACEHOLDER_MODULE}`**.",
                "",
            ),
        )
        self.edit(
            "AGENTS.md",
            "src and tests in the layout",
            lambda text: drop_lines(
                text, lambda line: line.startswith((f"src/{PLACEHOLDER_MODULE}/", "tests/  "))
            ),
        )
        self.edit(
            "docs/index.md",
            "the usage example",
            lambda text: drop_markdown_section(text, "## Use it"),
        )
        self.edit(
            "docs/index.md",
            "the tests in the check sentence",
            lambda text: _replace(
                text,
                "One command runs the linters, the type checker and the tests:",
                "One command runs the linters and the type checker:",
            ),
        )
        self.edit(
            "docs/index.md",
            "src and tests in the layout table",
            lambda text: drop_lines(
                text,
                lambda line: line.startswith((f"| `src/{PLACEHOLDER_MODULE}/`", "| `tests/`")),
            ),
        )

    def names_the_placeholder(self) -> bool:
        """Whether anything tracked still carries the placeholder, in a path or in its text."""
        for rel in self.repo.tracked:
            if PLACEHOLDER_MODULE in rel or PLACEHOLDER_MODULE in (self.repo.read(rel) or ""):
                return True
        return False

    def rename(self) -> None:
        """Run both substitutions over every tracked file that still names the placeholder."""
        module = self.identity.module
        if module is not None and (self.repo.root / f"src/{PLACEHOLDER_MODULE}").is_dir():
            self.repo.move(f"src/{PLACEHOLDER_MODULE}", f"src/{module}")
            self.say("renamed", f"src/{PLACEHOLDER_MODULE}/ -> src/{module}/")
        pairs = [(PLACEHOLDER_DIST, self.identity.dist)]
        if module is not None:
            pairs.append((PLACEHOLDER_MODULE, module))
        count = 0
        for rel in self.repo.tracked:
            text = self.repo.read(rel)
            if text is None:
                continue
            edited = text
            for old, new in pairs:
                edited = edited.replace(old, new)
            if edited != text:
                self.repo.write(rel, edited)
                count += 1
        self.say("renamed", f"the placeholder, in {count} tracked file(s)")

    def describe(self) -> None:
        """Put the one-line answer where the package and the site both read it."""
        description = self.identity.description
        self.edit(
            "pyproject.toml",
            "the package description",
            lambda text: _sub(text, r'(?m)^description = ".*"$', f'description = "{description}"'),
        )
        self.edit(
            "mkdocs.yml",
            "the site description",
            lambda text: _sub(
                text, r"(?m)^site_description: .*$", f'site_description: "{description}"'
            ),
        )
        self.edit(
            "docs/index.md",
            "the front page description",
            lambda text: _replace(
                text, PLACEHOLDER_DESCRIPTION.replace("this.", "this page."), description
            ),
        )

    def stamp_license(self) -> None:
        """Date the licence to the year this ran, rather than the year the template shipped."""
        year = date.today().year
        self.edit(
            "LICENSE",
            "the copyright year",
            lambda text: _sub(text, LICENSE_COPYRIGHT, f"Copyright (c) {year} Liu Lab"),
        )

    def write_readme(self) -> None:
        """Replace the template's README with this repo's own."""
        if not self.approved("README.md"):
            return
        self.repo.write("README.md", readme(self.identity))
        self.say("wrote", "README.md")

    def reset_changelog(self) -> None:
        """Empty the changelog of the template's entries, keeping the file and its preamble."""
        if not self.approved("CHANGELOG.md"):
            return
        self.edit(
            "CHANGELOG.md",
            "the template's own entries",
            lambda text: truncate_at(text, CHANGELOG_UNRELEASED),
        )

    def commit(self, *, do_commit: bool) -> None:
        """Stage everything, and record it as one commit."""
        self.repo.git("add", "-A")
        if not do_commit:
            self.say("staged", "every change, uncommitted, as asked")
            return
        message = (
            "Initialize this repo from the Liu Lab template\n\n"
            f"Renders the {self.identity.shape} shape as {self.identity.dist}."
        )
        self.repo.git(*self.repo.fallback_identity, "commit", "-q", "-m", message)
        self.say("committed", self.repo.git("log", "-1", "--pretty=%h %s").stdout.strip())

    def tag(self, *, do_tag: bool) -> str | None:
        """Create `v0.0.0`, unless this repo already tags something."""
        if not do_tag:
            return None
        existing = self.repo.git("tag", "-l").stdout.split()
        if existing:
            self.say("skipped", f"v0.0.0 — this repo already has tags ({existing[-1]})")
            return None
        self.repo.git(
            *self.repo.fallback_identity,
            "tag",
            "-a",
            "v0.0.0",
            "-m",
            "The version before the first",
        )
        self.say("tagged", "v0.0.0")
        return "v0.0.0"

    def push(self, tag: str | None) -> None:
        """Push the branch, and the tag if there is a new one."""
        branch = self.repo.git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.repo.git("push", "origin", branch)
        self.say("pushed", branch)
        if tag is not None:
            self.repo.git("push", "origin", tag)
            self.say("pushed", tag)


def readme(identity: Identity) -> str:
    """Build the README a rendered repo starts with: what it is, how to set it up, how to check it."""
    parts = [
        f"# {identity.dist}",
        "",
        identity.description,
        "",
        "## Set it up",
        "",
        "This repo uses [pixi](https://pixi.sh) and nothing else — no pip, no conda, no uv.",
        "Clone it, then:",
        "",
        "```bash",
        "pixi install",
        "```",
        "",
    ]
    if identity.module is not None:
        parts += [
            "## Use it",
            "",
            "```python",
            f"from {identity.module} import greet",
            "",
            'print(greet("lab"))',
            "```",
            "",
        ]
    if identity.command is not None:
        parts += [
            "The same thing from a shell:",
            "",
            "```bash",
            f"pixi run {identity.command} greet lab",
            "```",
            "",
        ]
    tail = "the linters, the type checker and the tests" if identity.module else "every check"
    parts += [
        "## Check your work",
        "",
        "```bash",
        "pixi run check",
        "```",
        "",
        f"That runs {tail}. It reports every failure at once, so read to",
        "the bottom before you fix anything.",
        "",
        "## Read the docs",
        "",
        f"The site is at <{identity.site_url}>.",
        "Build it yourself with `pixi run docs-build`.",
        "",
        "## Set up your agent",
        "",
        "Skills for coding agents live in `skills/`. Link them into each agent's own folder:",
        "",
        "```bash",
        "python skills/install.py --target all",
        "```",
        "",
        "If you work on the lab's clusters, add the shared plugin once per machine:",
        "",
        "```text",
        "/plugin marketplace add liuhlab/liulab-compute-skills",
        "/plugin install lab-compute@liulab",
        "```",
        "",
    ]
    return "\n".join(parts)


def ruleset_body(checks: Sequence[str]) -> str:
    """Build the ruleset a new repo's `main` gets: a pull request, and this shape's own checks.

    Zero approvals, because a repo worked headlessly by agents has no second reviewer: what is
    bought here is the pull request itself, which makes `pull_request` — the trigger that tests
    the merge commit — the gate every change goes through.
    """
    return json.dumps(
        {
            "name": "main",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [
                {
                    "type": "pull_request",
                    "parameters": {
                        "required_approving_review_count": 0,
                        "dismiss_stale_reviews_on_push": False,
                        "require_code_owner_review": False,
                        "require_last_push_approval": False,
                        "required_review_thread_resolution": False,
                        # Stated rather than left out: GitHub defaults this one to true, which
                        # adds a reviewer nobody asked for and blocks a headless merge.
                        "require_extra_approval_for_unattributed_changes": False,
                    },
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        # False, so a branch does not have to be rebased onto `main` to merge.
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": check} for check in checks],
                    },
                },
            ],
        },
        separators=(",", ":"),
    )


def next_steps(identity: Identity, tag: str | None, *, pushed: bool) -> list[str]:
    """List what is left for a person to do, including the parts that are not in this repo.

    The last two steps are GitHub state rather than tree state, so they are handed over as
    COMMANDS with this repo's own owner and name already in them. This script prints them and
    does not run them: it shells out to `git` alone and reaches the network only under `--push`,
    and calling `gh` here would buy a pure-local script an external tool, an unauthenticated
    failure path, and two more ways to half-succeed. The reader is the `init-repo` agent, still
    running, which has `gh`.
    """
    steps = ["Run `/init` to write `AGENTS.md` for this repo, then delete the sentinel line."]
    if not pushed:
        steps.append("Push the branch" + (f" and `{tag}`." if tag else "."))
    if identity.shape == "published":
        steps.append(
            f"Add a PyPI trusted publisher before the first release: project `{identity.dist}`, "
            f"owner `{identity.owner}`, repository `{identity.repo}`, workflow `release.yml`, "
            "environment `pypi`. There is no API token in this repo, and there must never be one."
        )
        steps.append("Publish a GitHub Release to release. Pushing a tag does not publish.")
    checks = SHAPE_CHECKS[identity.shape]
    steps.append(
        "Clone the lab's labels — a template copies none, so `--label ready-for-agent` fails "
        "until you do:\n"
        f"gh label clone {LABEL_SOURCE} --force --repo {identity.owner}/{identity.repo}"
    )
    steps.append(
        f"Require a pull request and this repo's own checks ({', '.join(checks)}) on `main`:\n"
        f"echo '{ruleset_body(checks)}' | "
        f"gh api --method POST repos/{identity.owner}/{identity.repo}/rulesets --input -"
    )
    steps.append("Turn on GitHub Pages for the `gh-pages` branch to publish the site.")
    return steps


# --------------------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Read the four interview answers, and the switches a caller that is not a person needs."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--shape", required=True, choices=SHAPES, help="Interview question 1.")
    parser.add_argument("--module", help="Interview question 2. Not used by --shape no-package.")
    parser.add_argument(
        "--cli",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Interview question 3. The command takes the module's name.",
    )
    parser.add_argument("--description", required=True, help="Interview question 4.")
    parser.add_argument("--remote", help="Override the URL read from `git remote get-url origin`.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="The repo to render. Defaults to the repo this script lives in.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Report which deletions and overwrites would run, and change nothing.",
    )
    parser.add_argument(
        "--force",
        action="append",
        default=[],
        metavar="PATH",
        help="Delete or overwrite this target even though it has changed. Repeatable.",
    )
    parser.add_argument(
        "--no-commit", action="store_true", help="Stage the changes, do not commit."
    )
    parser.add_argument("--no-tag", action="store_true", help="Do not create the v0.0.0 tag.")
    parser.add_argument("--push", action="store_true", help="Push the branch and the tag.")
    return parser.parse_args(argv)


def identity_from(args: argparse.Namespace, repo: Repo) -> Identity:
    """Derive what is stated rather than asked, and refuse an answer that cannot work."""
    module: str | None = args.module or None
    if args.shape == "no-package":
        if module is not None:
            raise SystemExit("init-repo: --module means nothing with --shape no-package")
        if args.cli:
            raise SystemExit("init-repo: --cli means nothing with --shape no-package")
    elif module is None:
        raise SystemExit(f"init-repo: --module is required with --shape {args.shape}")
    if module is not None and (not module.isidentifier() or keyword.iskeyword(module)):
        raise SystemExit(f"init-repo: `{module}` is not a name Python can import")
    url = args.remote or repo.git("remote", "get-url", "origin", check=False).stdout.strip()
    if not url:
        raise SystemExit(
            "init-repo: this repo has no `origin` remote, so the distribution name cannot be "
            "derived. Add the remote, or pass --remote."
        )
    owner, name = parse_remote(url)
    return Identity(
        owner=owner,
        repo=name,
        dist=name,
        module=module,
        command=module if args.cli else None,
        description=args.description.strip(),
        shape=args.shape,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Render the template into one repo, and say what it did to every file it touched."""
    args = parse_args(argv)
    root = args.root.resolve()
    repo = Repo(root)
    if repo.git("rev-parse", "--git-dir", check=False).returncode != 0:
        raise SystemExit(f"init-repo: {root} is not a git repository")
    identity = identity_from(args, repo)
    init = Init(repo=repo, identity=identity, forced={p.rstrip("/") for p in args.force})

    print(f"init-repo — rendering {identity.dist} ({identity.shape})\n")
    init.say("remote", f"{identity.owner}/{identity.repo}")
    init.say("module", identity.module or "none, this repo has no package")
    init.say("command", identity.command or "none")
    init.say("site", identity.site_url)

    dirty = repo.git("status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        raise SystemExit(
            "\ninit-repo: this repo has uncommitted changes. Commit or stash them first — this "
            "script commits what it does, and it must not sweep up work it did not make.\n"
            f"{dirty}"
        )

    init.survey()
    print("\n  the guarded set — a deletion or an overwrite runs only on an untouched target\n")
    for target in init.guarded:
        state = "untouched" if target.untouched else "CHANGED"
        init.say(state, f"{target.path} — would {target.verb}: {target.reason}")
    if args.plan:
        return 0
    init.confirm()

    print("\n  what it did\n")
    init.remove_template_only()
    init.prune_shared()
    if identity.shape == "no-package":
        init.prune_package()
    elif identity.command is None:
        init.prune_cli()
    release = ".github/workflows/release.yml"
    if identity.shape != "published" and init.approved(release):
        init.remove(release)

    if init.names_the_placeholder():
        init.rename()
    else:
        init.say("skipped", "the rename — nothing tracked names the placeholder any more")
    init.describe()
    init.stamp_license()
    init.write_readme()
    init.reset_changelog()

    if init.misses:
        print("\n  what it left alone\n")
        for miss in init.misses:
            init.say("missed", miss)

    print("")
    init.commit(do_commit=not args.no_commit)
    tag = init.tag(do_tag=not args.no_tag and not args.no_commit)
    if args.push:
        init.push(tag)

    print("\n  next\n")
    for step in next_steps(identity, tag, pushed=args.push):
        # A step may carry a command under it, on its own line, so the command is copy-pasteable
        # rather than buried in a sentence. Leading spaces are nothing to a shell.
        first, *command = step.split("\n")
        init.say("todo", first)
        for line in command:
            init.note(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
