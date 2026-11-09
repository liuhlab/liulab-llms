"""The command line. One verb and a version, so `[project.scripts]` has something to register.

Typer, because every lab repo that ships a command line uses it: one `typer.Typer` named
`app`, `no_args_is_help=True` so a bare invocation prints help instead of nothing, and a
`version` command. A larger surface grows by mounting sub-apps with `app.add_typer`.
"""

from typing import Annotated

import typer

from newpkg import __version__ as _package_version
from newpkg.core import greet as _greet

#: What `[project.scripts]` registers. Typer builds the parser from the signatures below, so
#: a verb is a function and its help is the docstring.
app = typer.Typer(help="The placeholder command.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the installed package version."""
    typer.echo(_package_version)


@app.command()
def greet(name: Annotated[str, typer.Argument(help="Who to greet.")] = "world") -> None:
    """Print a greeting addressed to NAME."""
    typer.echo(_greet(name))
