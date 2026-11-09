---
search:
  exclude: true
---

# Vale setup for the repo template

Resolves liuhlab/liulab-repo-template#3. Every claim below was run against Vale 3.19.0
(`pixi exec --spec vale==3.19.0`) over the six sibling repos. Numbers are Vale's, not estimates.

## 1. Packaging: commit `styles/` in-repo

**Recommendation: commit the `Lab` style under `styles/Lab/`. Do not use `Packages`.**

| | in-repo `styles/` | `Packages` + `vale sync` |
| --- | --- | --- |
| CI network | none | one fetch per run |
| Failure mode | none | CI red when GitHub is slow |
| Reviewability | rule change shows in the PR diff | invisible; lands out of band |
| Cost | one file to copy per repo | a release process for 3 YAML files |

`Packages` earns its keep when many repos share a large style that changes often. Three rules
totalling ~40 lines is the opposite case. A template repo is *copied*, so the style travels with
the copy for free. Revisit only if a fourth or fifth rule appears and repos start drifting.

Note `StylesPath` is normally gitignored (it holds downloaded packages). Here it is tracked —
so do **not** add `styles/` to `.gitignore`.

```ini
# .vale.ini
StylesPath = styles
MinAlertLevel = error

# Human-facing: plain language, readable prose.
[{README.md,docs/**/*.md}]
BasedOnStyles = Lab
Lab.Length = NO

# Agent-facing: word budget only. This section must come SECOND.
[{AGENTS.md,CONTEXT.md,docs/adr/*.md}]
BasedOnStyles = Lab
Lab.Jargon = NO
Lab.Readability = NO
Lab.Length = YES
```

**The `Lab.Length = YES` line is load-bearing.** Vale's `*` matches `/`, so `docs/**/*.md`
also matches `docs/adr/*.md`. Both sections match an ADR, and per-rule settings *accumulate*
across matching sections — so without the explicit re-enable, ADRs inherit `Lab.Length = NO`
from section one and are checked by **nothing at all**. Verified: with the line removed, ADRs
produce zero alerts and CI passes green on an empty gate.

## 2. The three rules

```yaml
# styles/Lab/Jargon.yml  — see the swap map in §3
extends: substitution
message: "Jargon: prefer '%s' over '%s'."
level: error
ignorecase: true
swap: { ... }
```

```yaml
# styles/Lab/Readability.yml
extends: readability
message: "Reading grade (%s) is above 11; shorten sentences, not domain terms."
level: error
grade: 11
metrics:
  - Flesch-Kincaid
```

```yaml
# styles/Lab/Length.yml
extends: metric
message: "This file is %s words; the cap is 400. Split it."
level: error
formula: words
condition: "> 400.0"
```

Confirmed against docs and by running it:

- `metric` + `formula: words` + `condition` **works per file**. All `metric` rules are
  `summary`-scoped because the variables are computed over the whole document; the alert is
  reported at `1:1`. Observed output: `File is 88.00 words; the cap is 40.`
- Conditions must be **floating point** — `"> 400.0"`, not `"> 400"`.
- `.vale.ini` glob sections **do** scope different rules to different files, with the
  accumulation caveat above. `BasedOnStyles` *replaces* across sections; named rules *accumulate*.
- `readability` is the one extension point that takes no `scope`, and Vale skips fenced code
  blocks by default — so code comments are not linted (this is why `idempotent` in
  `liulab-data/README.md:34` and `hermetic` in `seqforge/README.md:50` never fire).

## 3. The jargon list

I read the README and human-facing `docs/` of all six repos. **The corpus is already clean of
corporate jargon**: zero hits for *leverage, utilize, facilitate, robust, seamless, ecosystem,
paradigm, streamline, bespoke, in order to, first-class, turnkey, battle-tested*. A generic
anti-corporate list would be pure dead weight here. The real offenders are architecture-speak
and needlessly Latinate verbs. This map produces **32 hits, zero false positives**.

```yaml
swap:
  '(?:public|API|import|package)\s+surface': public API
  'surfac(?:e|es|ed)\s+(?:it\s+)?as': reported as
  'content[- ]address(?:ed|ing)': named by its hash
  'shared (?:lab )?assets?': shared lab images
  'guardrails baked in': safety rules built in
  'degenerate case': simplest case
  'wire formats?': file format
  'single source of truth': the one place it is defined
  'out of the box': with no extra setup
  'helps to': helps
  '\bco-location\b': on the same machine
  '\bactionable\b': you can act on
  '\badditionally\b': also
  '\bexecutes?\b': run
  '\bperturbs?\b': change
  '\binterrogates?\b': asks
  '\barbitrates?\b': settle
  '\bmemoi[sz]e[sd]?\b': cache
  '\bsubstrate\b': what it runs on
  '\bREPL\b': interactive session
```

Evidence (one per pattern, all verified):

| Hit | Fix |
| --- | --- |
| `liulab-genome/docs/reference.md:14` "one package's public surface" | public API |
| `liulab-data/docs/tutorials/understanding-geo.md:41` "`labdata` surfaces it as a `BioSample` field" | reports it as |
| `seqforge/README.md:22` "immutable, content-addressed" | named by its own hash |
| `liulab-compute-skills/docs/index.md:21` "guardrails baked in" | safety rules built in |
| `seqforge/docs/concepts/artifacts.md:95` "is the degenerate case: one assay" | the simplest case |
| `eutely/docs/wormbase.md:61` "six sources in four wire formats" | file formats (nothing goes over a wire) |
| `liulab-runtime/docs/containers.md:137` "finds `STAR` out of the box" | with no extra setup |
| `liulab-runtime/docs/index.md:96` "execute a single command" | run (the command is literally `pixi run`) |
| `seqforge/docs/concepts/refusal.md:28` "an actionable remedy" | a remedy you can act on |
| `seqforge/docs/concepts/refusal.md:55` "interrogates you constantly" | keeps asking you questions |
| `eutely/docs/wormbase.md:55` "both loaders memoise on the release" | cache per release |
| `eutely/docs/deconv.md:11` "the substrate for the … comparison" | what the comparison runs on |

### The distinction that matters: what must be KEPT

The crux is that **"surface" is both the worst offender and a term that must survive**.
`eutely/docs/anatomy.md` says "825 surfaces", "the evaluated surface" — that is Blender
geometry, and a blanket `surface: API` swap would corrupt the page. Hence the patterns above
match only the *verb* (`surfaces it as`) and the *compound noun* (`API surface`). Verified: zero
hits in `anatomy.md`.

Kept deliberately, with evidence they are real vocabulary and not padding:

- **Domain terms** — FASTQ, BAM, GTF, FASTA, aligner, STAR, chromap, GEO/GSE/GSM/SRX, accession,
  assembly, annotation, ortholog, motif, transcription factor, UMI, WormBase, anndata, syncytium.
- **`manifest`, `artifact`, `probe`, `gate`, `rung`** — seqforge's own defined nouns, each
  introduced with a definition. Renaming them would break the docs, not fix them.
- **`canonical`** (6 hits, all liulab-genome) — means "the one official identifier form" in
  cross-reference resolution. Genuine domain usage, not architecture-speak.
- **`provenance`** (12+ hits) — used consistently for "where the bytes came from". Consistent
  usage *is* vocabulary; there is no shorter exact word.
- **`deterministic`, `immutable`, `preemptible`** — load-bearing precise claims. seqforge's whole
  thesis is "deterministic code owns every decision"; softening it loses the point.
- **`under the hood`, `baked in`, `load-bearing`** — plain-English idiom, shorter than any
  replacement. Idiom is not jargon.

Two gaps worth knowing: filler words (`simply`, 3 hits) are a *different* problem from jargon and
do not belong in a substitution map — the message would read "prefer '' over 'simply'". And
`seqforge/docs/kb/*.md` are one-line snippet includes; the prose they render lives in
`src/seqforge/kb/specs/*/README.md`, outside `docs/`, so five published pages go unchecked.

## 4. The readability number: cap at grade 11

Measured with Vale's own Flesch-Kincaid (`readability`, `grade: -99` to force the score out).

| README | FK grade |
| --- | --- |
| liulab-genome | 6.38 |
| liulab-runtime | 8.20 |
| liulab-data | 8.49 |
| eutely | 8.61 |
| liulab-compute-skills | 9.18 |
| seqforge | **10.12** |

Spread across all 56 human-facing docs: **3.72 to 16.88**, median 7.95.

**Cap at 11.** Every README passes today, with the tightest (seqforge, 10.12) keeping ~0.9 grades
of headroom. It fails exactly three real pages — `seqforge/docs/kb/index.md` (11.60) and
`eutely/docs/deconv.md` (11.49) — plus liulab-genome's seven `docs/context/*.md` glossaries
(12.19–16.88). Those seven are agent-facing context files misfiled under `docs/`; **move them to
match the `CONTEXT.md` convention** rather than loosening the cap for them. Grade 12 would be a
gate that catches nothing; grade 10 would fail seqforge on day one.

One honest tension: Flesch-Kincaid punishes polysyllabic words, which is exactly the domain
vocabulary Rule 3 protects. "Transcription factor" cannot be shortened. Keep the cap loose and
let `Lab.Jargon` do the word-choice work; use `Lab.Readability` to catch long *sentences*.

## 5. Invocation: all three

One gate, three entry points. The rules live in `.vale.ini`, so every path enforces the same thing.

```toml
# pixi.toml
[dependencies]
vale = "3.19.0.*"

[tasks]
vale = "vale ."
```

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: vale
      name: vale
      entry: pixi run -- vale
      language: system
      types: [markdown]
      pass_filenames: true
```

```yaml
# .github/workflows/ci.yml
- uses: prefix-dev/setup-pixi@v0.9.1
  with:
    locked: true
- name: Vale
  run: pixi run vale
```

`language: system` means pre-commit will not install Vale; `entry: pixi run --` puts it on PATH
from the pixi env, matching the lab's existing hook pattern.

**Gotcha that silently disables the gate:** Vale matches path globs against the filename *as
passed*, relative to the working directory. Passing relative paths works
(`vale docs/adr/0001-x.md` → alerts). Passing **absolute** paths matches no section at all and
exits 0 with zero output. So the hook must run from the repo root and pass repo-relative names —
which is pre-commit's default, but any wrapper script that resolves paths first will turn the
gate into a no-op. Only `error`-level alerts set a non-zero exit code; all three rules are
`level: error`, and `MinAlertLevel = error` keeps the output to what actually fails.
