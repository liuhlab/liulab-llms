"""The one function this placeholder carries, so the gates have something to act on."""


def greet(name: str = "world") -> str:
    """Return a greeting addressed to `name`.

    The numpydoc section form is demonstrated once here, by `Examples`; a function this small
    would not normally carry a section at all.

    Examples
    --------
    >>> greet("lab")
    'Hello, lab!'
    """
    return f"Hello, {name}!"
