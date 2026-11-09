"""The placeholder package. Rename this directory first; `init-repo` does it for you."""

from importlib.metadata import version

from newpkg.core import greet

#: Read from the installed distribution's metadata, which hatch-vcs fills from the newest
#: git tag. No version string is written by hand anywhere in this repo.
__version__ = version("liulab-newpkg")

__all__ = ["__version__", "greet"]
