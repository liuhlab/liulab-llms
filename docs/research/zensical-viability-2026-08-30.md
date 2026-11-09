---
search:
  exclude: true
---

# Zensical vs mkdocs-material for the lab template

Researched 2026-08-30 against zensical 0.0.57. All claims below marked **[tested]** were
verified by building a synthetic lab-shaped project (mkdocs.yml + `src/` package with
numpy docstrings) against `zensical==0.0.57` + `mkdocstrings-python==2.0.7`.

## Verdict: adopt with named workarounds — but as opt-in, not the template default

Zensical builds our config unchanged and mkdocstrings works, including numpy style.
Three things must be worked around: no `gh-deploy` (needs a hand-written Actions
workflow), `exclude_docs:` is silently ignored, and a nav entry naming a missing file
no longer fails `--strict`. Combined with a 0.0.x alpha shipping weekly with `!`-marked
breaking commits, this is not yet safe as the default for three production repos.

## 1. Install/pin under pixi

`zensical` 0.0.57 is on conda-forge for linux-64/aarch64/ppc64le, osx-64/arm64, win-64
([anaconda.org API](https://api.anaconda.org/package/conda-forge/zensical));
`mkdocstrings-python` 2.0.7 is too
([API](https://api.anaconda.org/package/conda-forge/mkdocstrings-python)). No bioconda needed.

```toml
[tool.pixi.feature.docs.dependencies]
zensical = "==0.0.57"
mkdocstrings-python = ">=2.0.7,<3"
```

```toml
[tool.pixi.feature.docs.tasks]
docs = "zensical serve"
docs-build = "zensical build --clean --strict"
```

`serve` accepts `-s/--strict` but prints "Warning: Strict mode is currently unsupported"
and ignores it — omit it
([main.py L105-122](https://github.com/zensical/zensical/blob/master/python/zensical/main.py)).
`--clean` is what the official CI workflow uses
([publish-your-site](https://zensical.org/docs/publish-your-site/)).

## 2. Config

It reads `mkdocs.yml` directly. With no `-f`, autodiscovery tries `zensical.toml`,
`mkdocs.yml`, `mkdocs.yaml` in that order
([main.py L71-76](https://github.com/zensical/zensical/blob/master/python/zensical/main.py)),
so both can coexist — `zensical.toml` wins, and `-f mkdocs.yml` overrides. Docs confirm
"Use your existing `mkdocs.yml`. No need to create a `zensical.toml`"
([compatibility](https://zensical.org/compatibility/)). `!ENV` and `INHERIT` are supported
([config.py](https://github.com/zensical/zensical/blob/master/python/zensical/config.py)).

Minimal equivalent ([bootstrap zensical.toml](https://github.com/zensical/zensical/blob/master/python/zensical/bootstrap/zensical.toml)):

```toml
[project]
site_name = "liulab-data"
repo_url = "https://github.com/liuhlab/liulab-data"
nav = [
  { Home = "index.md" },
  { API = "api/index.md" },
]
```

`theme.name` is unnecessary: both `material` and `zensical` resolve to the built-in theme
([config.py `get_theme_dir`](https://github.com/zensical/zensical/blob/master/python/zensical/config.py)).

## 3. Publishing to gh-pages — HARD CONSTRAINT, workaround required

**No `gh-deploy` equivalent, and it will not be built.** "gh-deploy command: we plan to
support many more deployment methods, so we're not implementing MkDocs' gh-deploy command"
([compatibility/cli](https://zensical.org/compatibility/cli/)). The official workflow
uses `actions/upload-pages-artifact` + `actions/deploy-pages`, which bypasses `gh-pages`
entirely ([publish-your-site](https://zensical.org/docs/publish-your-site/)).

To keep the `gh-pages` branch:

```yaml
name: docs
on:
  push:
    branches: [main]
permissions:
  contents: write
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7          # v7.0.1
      - uses: prefix-dev/setup-pixi@v0.10.2
        with:
          environments: docs
      - run: pixi run -e docs docs-build   # writes ./site
      - uses: peaceiris/actions-gh-pages@v4  # v4.1.0
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./site
          publish_branch: gh-pages
          force_orphan: true               # matches `gh-deploy --force`
```

## 4. mkdocstrings — supported [tested]

Supported since 0.0.11, declared "preliminary"
([setup/extensions/mkdocstrings](https://zensical.org/docs/setup/extensions/mkdocstrings/)).
The `mkdocstrings` plugin block in `mkdocs.yml` is shimmed into a Markdown extension and
the handler config is passed through verbatim, so all `mkdocstrings-python` options apply
([config.py `_shim_mkdocstrings`](https://github.com/zensical/zensical/blob/master/python/zensical/config.py),
[compat/mkdocstrings.py](https://github.com/zensical/zensical/blob/master/python/zensical/compat/mkdocstrings.py)).

Verified rendering with `paths: [src]`, `docstring_style: numpy`, `separate_signature`,
`show_symbol_type_heading`, `signature_crossrefs`: parameter tables, symbol headings,
source blocks, `objects.inv`, and `[x][mypkg.add]` cross-refs all render. `inventories:`
downloads work. **[tested]**

What is lost:

| Item | Status | Source |
| --- | --- | --- |
| Backlinks | Not supported | [docs](https://zensical.org/docs/setup/extensions/mkdocstrings/) |
| Watching `paths` outside project root | Not watched; no rebuild on edit | [docs](https://zensical.org/docs/setup/extensions/mkdocstrings/) |
| `gen-files` / `literate-nav` | Backlog, unimplemented (Tier 2 / Tier 1) | [compatibility/plugins](https://zensical.org/compatibility/plugins/) |

`paths: [src]` inside the repo is watched fine
([config.py `_list_sources`](https://github.com/zensical/zensical/blob/master/python/zensical/config.py)).

## 5. Strict mode

Flag is `zensical build --strict` (`-s`), or `strict: true` in `mkdocs.yml` since 0.0.53
([v0.0.53 notes](https://github.com/zensical/zensical/releases/tag/v0.0.53),
[setup/validation](https://zensical.org/docs/setup/validation/)).

| Failure mode | Strict result |
| --- | --- |
| Dead internal link | Warns, exits 1 **[tested]** |
| Dead anchor (`page.md#nope`) | Warns (`invalid_link_anchors`, on by default) — [docs](https://zensical.org/docs/setup/validation/) |
| Unresolvable mkdocstrings/autoref | Warns "unresolved autoref", exits 1 **[tested]** |
| **Nav entry naming a missing file** | **Silent. Build succeeds, dead nav link rendered [tested]** |
| Missing `pymdownx.snippets` file | Hard `SnippetMissingError`, exits 1 **[tested]** |

The nav case is a regression from `mkdocs build --strict`. `validation.nav.*` keys from
MkDocs are dropped: only link settings are mapped
([config.py validation block](https://github.com/zensical/zensical/blob/master/python/zensical/config.py) —
"we only support validation of links right now").

## 6. Extension parity — all pass except `exclude_docs` [tested]

Zensical runs real Python-Markdown 3.10 and pymdown-extensions 11.0.2 in-process, so
extension behaviour is identical by construction
([pyproject requires_dist](https://pypi.org/pypi/zensical/json)).

| Feature | Result |
| --- | --- |
| admonition, attr_list, md_in_html | OK |
| pymdownx.details, .highlight, .inlinehilite | OK |
| pymdownx.snippets (`base_path`, `check_paths`) | OK; missing file fails build |
| pymdownx.superfences + mermaid `custom_fence` | OK (`class="mermaid"` emitted; also a default) |
| pymdownx.tabbed (`alternate_style`) | OK |
| toc `permalink` | OK |
| search | OK (`site/search.json`, search worker) |
| GA4 via `extra.analytics` | OK (gtag + `G-…` emitted); [docs](https://zensical.org/docs/setup/analytics/) |
| **`exclude_docs:`** | **Silently ignored — excluded pages are published** |

`exclude_docs` appears nowhere in the source; the `exclude` plugin is Tier 2 backlog
([compatibility/plugins](https://zensical.org/compatibility/plugins/)). Workaround: keep
drafts outside `docs_dir`.

Also missing from Material parity: social cards, tag listings, blog, data privacy, the
module/hook system ([compatibility/features](https://zensical.org/compatibility/features/)).

## 7. Alpha risk

Last 8 releases ([PyPI JSON API](https://pypi.org/pypi/zensical/json)):

| Version | Date | Version | Date |
| --- | --- | --- | --- |
| 0.0.50 | 2026-07-09 | 0.0.54 | 2026-08-13 |
| 0.0.51 | 2026-07-17 | 0.0.55 | 2026-08-16 |
| 0.0.52 | 2026-07-30 | 0.0.56 | 2026-08-18 |
| 0.0.53 | 2026-08-04 | 0.0.57 | 2026-08-21 |

Roughly weekly, tightening to every 2-3 days in August. Documented churn:
0.0.48 shipped a regression that broke search, hotfixed by 0.0.50 the same day
([v0.0.50](https://github.com/zensical/zensical/releases/tag/v0.0.50));
0.0.56 deprecated `unresolved_references` outright
([v0.0.56](https://github.com/zensical/zensical/releases/tag/v0.0.56));
`master` already carries a post-0.0.57 breaking commit
`refactor!: remove x86 and armv7 musl builds` (34043fe). Classifier is
`Development Status :: 3 - Alpha`, and the module/plugin API is deliberately unreleased
([compatibility/plugins](https://zensical.org/compatibility/plugins/)).

**Pin `zensical = "==0.0.57"` — exact, not a range.** At 0.0.x there is no semver
guarantee that `0.0.58` is compatible, and 0.0.48→0.0.50 proves a patch bump can break a
core feature within hours. Bump deliberately and re-run `docs-build --strict`.

## Gaps

- Nav validation: I confirmed by test that a missing nav target does not fail; I found no
  issue-tracker entry stating whether this is intended or planned.
- Backlinks: only the docs' one-line statement; no issue or code confirmation of scope.
- I did not install the conda-forge package, so I did not verify it ships the `zensical`
  console script (the PyPI wheel does). Verify before committing the pixi lines.
- No `CHANGELOG.md` exists in the repo; release notes live only on GitHub Releases.
- `https://zensical.org/llms-full.txt` and `https://zensical.org/docs/sitemap.md` both 404.
- Untested here: `mike` versioning, `zensical serve` live-reload against `src/`, and
  build behaviour on a real lab repo (liulab-data, liulab-genome, eutely).

## Follow-up: docs_dir and unpublished subtrees

**1. `docs_dir` is honoured.** Same literal key in both formats — `docs_dir: content/site`
in `mkdocs.yml`, `docs_dir = "content/site"` under `[project]` in `zensical.toml`. Both
built clean into `./site` **[tested]**. Two constraints, enforced at config load
([config.py `_apply_defaults`](https://github.com/zensical/zensical/blob/master/python/zensical/config.py)):
`docs_dir` must resolve *inside* the project root (so no `../`), and must differ from
`site_dir`. That leaves any in-repo subdirectory layout available.

**2. No mechanism exists today to keep a subtree out of the built site.** Every file under
`docs_dir` is published, whether or not it appears in `nav` **[tested]**. Searching the
source rather than the docs:

- Front matter honoured by the Rust side is only `title`, `template`, `tags`
  (`meta.get(...)` in `crates/zensical/src/structure/`); templates additionally read
  `hide`, `description`, `author`, `location`. `hide:` suppresses UI chrome, not the page —
  a `draft: true` + `hide:` page still emitted `site/drafts/draft/index.html` **[tested]**.
- No `.pages` handling, no `not_in_nav`, no ignore/exclude/glob patterns, no per-directory
  config anywhere in `crates/` or `python/`. The only `.gitignore` write is for the build
  cache (`config.rs` `get_cache_dir`).
- `awesome-nav` (`.pages`), `literate-nav` and `exclude` are all unimplemented backlog
  items ([compatibility/plugins](https://zensical.org/compatibility/plugins/)); nothing
  states what `exclude` will support.

Only workaround: keep unpublished content outside `docs_dir`.

**3. A missing `docs_dir` is a hard error**, not a warning: `Error: Docs directory does not
exist: <abs path>`, exit 1, before any build work **[tested]**.

## Follow-up: nav autogeneration and search visibility

**1. Nav is auto-generated from the directory tree when `nav:` is absent.** With no `nav:`
key, a three-page site produced a navbar containing `Home`, `guide/` *and*
`adr/0001-manifest/` **[tested]**. So eutely's current "no explicit nav" setup would put
`docs/adr/`, `docs/research/` and `docs/agents/` straight into the navbar.
**The template must ship an explicit `nav:`.**
([nav.rs `from(pages)`](https://github.com/zensical/zensical/blob/master/crates/zensical/src/structure/nav.rs)
mirrors MkDocs' auto-population.)

**2. A published page absent from `nav` IS indexed by default.** Adding the ADR produced a
third entry in `site/search.json` with its body text searchable **[tested]** — exactly the
"searching *manifest* surfaces an ADR" case.

**3. Yes — `search: exclude: true` front matter works, and it is the fix.** Material's
convention is honoured verbatim:

```yaml
---
search:
  exclude: true
---
```

With it, the page dropped out of `search.json` entirely (2 items, body token absent);
without it, 3 items **[tested]**. Implemented at
[render.py L145-147](https://github.com/zensical/zensical/blob/master/python/zensical/markdown/render.py)
(`if meta.get("search", {}).get("exclude", False): search_processor.data = []`).
Block-level `data-search-exclude` via `attr_list` also works
([extensions/search.py L146-148](https://github.com/zensical/zensical/blob/master/python/zensical/extensions/search.py)).

Two residues: an excluded page still appears in `sitemap.xml` **[tested]**, so search
engines will index it; and front matter does not remove it from an auto-generated navbar —
only an explicit `nav:` does.

**Correction, 2026-08-31 (from #23, implementing this note).** The sitemap residue is
conditional on the navbar being auto-generated. With an explicit `nav:` present, zensical
builds `sitemap.xml` from the nav, not from the file tree: a template build with two nav
entries and seven unlisted pages produced a two-entry sitemap **[tested]**. The other claims
here held exactly as written, including that a page with neither a nav entry nor front
matter is published *and* indexed. The conda-forge package does ship the `zensical` console
script, which closes the gap this note left open.
