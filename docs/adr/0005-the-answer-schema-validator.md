---
search:
  exclude: true
---

# The answer schema validator

An answer that does not fit its kind's answer schema ends the unit unfinished. The plain
install must stay small.

**fastjsonschema.** It is pure Python with no dependencies of its own, and is on conda-forge.
`UnitKind` compiles its schema once, when the kind is declared, so a schema that is not JSON
Schema fails at once. It compiles with `use_default=False`, so it never fills a schema's
defaults into an answer. The answer is checked after a round trip through JSON, which is how
it is stored.

The `jsonschema` package covers every draft but brings four more packages, one of them
compiled Rust. Writing a validator in the standard library would mean a partial one.

## Consequences

- fastjsonschema knows drafts 4, 6 and 7. A keyword from a later draft, such as `prefixItems`
  or `unevaluatedProperties`, is silently ignored, so an answer schema should keep to draft 7
  keywords.
- Reviews will be checked by the same compiled schema.
