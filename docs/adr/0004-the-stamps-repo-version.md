---
search:
  exclude: true
---

# The stamp's code version for the calling repo

Every answer is stamped with the calling repo's code version, filled in with nothing for the
repo to pass.

**`git describe` of the checkout that defines the kind.** At the start of a pass the engine
runs `git describe --tags --always --dirty --abbrev=12` in the folder of the module holding
the kind's function. That names the commit exactly, with `-dirty` when the checkout has
uncommitted changes.

**Then the installed version, then "unknown".** When git is missing or the module is not in
a checkout, the stamp takes the version of the installed distribution that provides the
module, and failing that, `unknown`.

The installed version was the simpler choice, but an editable install keeps the version it
was installed with, so a repo worked on through pixi would stamp a stale commit.

## Consequences

- One `git` call per pass.
- A kind defined in a package installed inside another repo's checkout stamps that
  checkout's version.
- liulab-llms's own version comes from its installed metadata, since the engine is not the
  calling repo.
