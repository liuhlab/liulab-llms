---
search:
  exclude: true
---

# GitHub template mechanics

Research for liuhlab/liulab-repo-template#4, 2026-08-30. What `gh repo create
--template` gives a new repo, and what `init-repo` must build itself.

GitHub's docs cover only file and branch copying. Everything else is inferred from that
silence or checked against live repos; each claim says which.

## 1. What copies

Template generation copies **the git tree of the default branch**, nothing more.
"You can choose to include the directory structure and files from only the default
branch of the template repository or to include all branches."
([creating-a-repository-from-a-template](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template))

Because the unit is the git tree, anything git tracks copies:

| Thing | Copies? | Basis |
| --- | --- | --- |
| `.github/workflows/` | Yes | Ordinary tracked files. Unlike forks, generated repos are independent, so scheduled workflows run. |
| Dotfiles and dot-directories (`.claude/`, `.vale.ini`, `.pre-commit-config.yaml`) | Yes | Ordinary tracked files. |
| Files with no extension (`LICENSE`, `justfile`) | Yes | Ordinary tracked files. |
| Symlinks (`CLAUDE.md -> AGENTS.md`) | Expected yes — **UNVERIFIED, needs empirical check** | Git stores a symlink as a blob with mode `120000` (verified locally; also live in `liuhlab/bolero`, `liulab-genome`, `liulab-roadmap`, `liulab-compute-skills`, and `seqforge`'s `.agents/skills/*`). A tree copy preserves mode bits, but no GitHub doc says so. |
| Empty directories | **No** | Git has no empty-tree entries — verified locally: `mkdir emptydir && git add -A` records nothing. |
| Git LFS files | **No** | "Your template repository cannot include files stored using Git LFS." ([creating-a-template-repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository)) |

Empty-directory workaround: commit a placeholder (`.gitkeep`) inside it, or have
`init-repo` `mkdir` it. A placeholder is preferable — it survives every clone.

## 2. What does not copy

The docs never enumerate this. The generate endpoint's own description scopes it to
content: "Creates a new repository using a repository template."
([REST: create a repository using a template](https://docs.github.com/en/rest/repos/repos#create-a-repository-using-a-template))
Everything below is repository state stored outside the git tree, so none of it copies.

| Item | Carries over? | Confidence |
| --- | --- | --- |
| Issues, pull requests, milestones, discussions | No | High — not tree content |
| Labels | No — see below | **Verified** by docs |
| Actions secrets and variables | No | High; corroborated by [community #144277](https://github.com/orgs/community/discussions/144277) |
| Branch protection / rulesets | No | High; corroborated by [community #55200](https://github.com/orgs/community/discussions/55200) |
| Pages configuration | No | High — **UNVERIFIED, needs empirical check** |
| Wiki content, releases, tags | No | High (tags: see §3) |
| Collaborators, webhooks | No | High |
| Sub-issue / issue-dependency settings | N/A | Sub-issues need no per-repo enablement ([adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)); org-level issue types are inherited from `liuhlab`, not the template |

**Labels, precisely.** A generated repo is a *new* repo, and "Default labels are
included in every new repository when the repository is created."
([managing-labels](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels))
Verified against `liuhlab/liulab-data`, `bolerodata`, `MOODS_pixi`: each has exactly
the nine stock labels (`bug`, `documentation`, `duplicate`, `enhancement`,
`good first issue`, `help wanted`, `invalid`, `question`, `wontfix`). The docs list a
tenth, `accessibility`; older repos lack it, so new repos may get ten.

`wontfix` already exists stock as `#ffffff` / "This will not be worked on". The lab's
`wontfix` says "Will not be actioned", so it must be *overwritten*, not created.

Simplest correct invocation — one command, clones the template's live label set:

```sh
gh label clone liuhlab/liulab-repo-template --force --repo liuhlab/<name>
```

`--force` overwrites `wontfix`; without it that label is silently skipped
(`gh label clone --help`, gh 2.95.0). Explicit equivalent, if the template is ever not
the source of truth:

```sh
gh label create needs-triage       -c d93f0b -d "Maintainer needs to evaluate this issue"
gh label create needs-info         -c fbca04 -d "Waiting on reporter for more information"
gh label create ready-for-agent    -c 0e8a16 -d "Fully specified, ready for an AFK agent"
gh label create ready-for-human    -c 1d76db -d "Requires human implementation"
gh label create wontfix            -c ffffff -d "Will not be actioned" --force
gh label create wayfinder:map      -c 5319e7 -d "The wayfinder map for an effort"
gh label create wayfinder:research -c 006b75 -d "AFK: resolved by a /research subagent"
gh label create wayfinder:grilling -c b60205 -d "HITL: resolved by conversation"
gh label create wayfinder:prototype -c e99695 -d "HITL: resolved by building a rough artifact"
gh label create wayfinder:task     -c c5def5 -d "Manual work that unblocks a decision"
```

## 3. Git history and hatch-vcs

History is **squashed**: "a repository created from a template starts with a single
commit." Branches from a template "have unrelated histories, which means you cannot
create pull requests or merge between the branches."
([same page](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template))

Tags therefore **cannot** carry — a tag names a commit, and the template's commits do
not exist in the new repo. No doc states this outright; it follows from the single
commit. **UNVERIFIED, needs empirical check**, but there is no mechanism for it.

The hatch-vcs consequence is the sharp edge. `fallback-version` is "the version that
will be used if no other method for detecting the version is successful"
([hatch-vcs](https://github.com/ofek/hatch-vcs)) — and setuptools-scm uses it only
"when using a tarball with no metadata"
([config](https://setuptools-scm.readthedocs.io/en/latest/config/)). A cloned repo *is*
an SCM checkout, so detection succeeds; it just has no tag. Verified locally on a
one-commit, zero-tag repo:

```text
NO-TAG VERSION: 0.1.dev1+g8361f6d77
```

So a fresh lab repo builds as **`0.1.dev1+g<sha>`**, not `0.0.0+dev`. That is a wrong,
higher-than-intended version that will silently ship. `init-repo` must create and push
a starting tag:

```sh
git tag -a v0.0.0 -m "Initial version" && git push origin v0.0.0
```

After that, the working tree reports `0.0.1.dev<n>+g<sha>` — next version is the tag
with 1 added to its last component
([setuptools-scm usage](https://setuptools-scm.readthedocs.io/en/latest/usage/)).

## 4. Branch selection

No. The API takes no branch parameter — only `owner`, `name`, `description`,
`private`, and `include_all_branches`: "Set to true to include the directory structure
and files from all branches in the template repository, and not just the default
branch" (default `false`).
([REST reference](https://docs.github.com/en/rest/repos/repos#create-a-repository-using-a-template))

`gh` exposes it as `--include-all-branches`, "Include all branches from template
repository" ([gh repo create](https://cli.github.com/manual/gh_repo_create)), guarded
by `the --include-all-branches option is only supported when using --template`
([cli/cli create.go](https://github.com/cli/cli/blob/trunk/pkg/cmd/repo/create/create.go)).
It is all-or-default-only; you cannot target one non-default branch.

Also from `create.go`: non-interactive runs need `--public`, `--private`, or
`--internal`, and `--template` rejects `--add-readme`, `--team`, and `--source`.

## 5. gh-pages and Pages

The `gh-pages` **branch** carries over only with `--include-all-branches`. The Pages
**setting** does not — it is repo configuration, not tree content
(**UNVERIFIED, needs empirical check**; `liulab-repo-template` has `has_pages: false`,
so it cannot be confirmed from the template itself).

Ordering constraint is documented: "Make sure the branch you want to use as your
publishing source already exists in your repository."
([configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site))
So Pages must be enabled *after* `gh-pages` exists — either copied by
`--include-all-branches` or pushed by the first docs build.

```sh
gh api --method POST repos/liuhlab/<name>/pages \
  -f 'build_type=legacy' \
  -f 'source[branch]=gh-pages' \
  -f 'source[path]=/'
```

`201` on success; `409` if Pages is already configured (use `PUT` to change it), `422`
on validation failure — e.g. a missing branch
([REST: Pages](https://docs.github.com/en/rest/pages/pages)). Requires admin or
maintainer, and a token with `repo` scope. Treat `409` as success in `init-repo`.

## 6. What init-repo must do

1. Create with visibility and, if `gh-pages` is wanted up front,
   `--include-all-branches`:
   `gh repo create liuhlab/<name> --template liuhlab/liulab-repo-template --public --clone`.
2. Rewrite identity: repo name, description, homepage, `README.md` title, package name
   and paths in `pyproject.toml`, and any `liulab-repo-template` string left in the tree.
3. `gh label clone liuhlab/liulab-repo-template --force` — the ten lab labels; stock
   defaults are already there and are left alone.
4. Delete or keep stock labels deliberately (`bug`, `enhancement`, … are not lab roles).
5. Create and push `v0.0.0` so hatch-vcs does not emit `0.1.dev1+g<sha>`.
6. Recreate any empty directories the template needed (or ship `.gitkeep` files).
7. Set repo settings the template cannot carry: issues on, wiki off, projects,
   squash-merge policy, auto-delete branch on merge.
8. Apply branch protection or a ruleset on `main`.
9. Add Actions secrets and variables (none copy).
10. Enable Pages from `gh-pages` — after the branch exists; tolerate `409`.
11. Seed the wayfinder map issue and any bootstrap issues; none copy.
12. Add collaborators or team access (`--template` forbids `--team` at create time).
13. Verify symlinks survived (`git ls-files -s | grep 120000`) until that is confirmed.
