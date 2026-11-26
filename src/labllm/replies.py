"""Reading a JSON value out of a chat reply that was sent with no schema."""

import json
from typing import Any

FENCE = "```"


class ReplyNotJson(ValueError):  # noqa: N818 - the name callers catch
    """A reply is neither JSON nor exactly one fenced block of JSON. The message names which."""


def reply_json(text: str) -> Any:
    """Return the JSON value a reply holds, by one strict rule, or raise `ReplyNotJson`.

    The whole text, surrounding whitespace ignored, is read as JSON first. Failing that, a
    text with exactly two fence markers is read as the JSON between them, less the rest of the
    opening marker's line, such as a language word; prose outside the fence is ignored.
    Nothing else is read: no object is picked out of prose, and no first of several blocks is
    taken. The value is not checked against any schema.

    Examples:
        >>> reply_json('Here:\\n```json\\n{"a": 1}\\n```')
        {'a': 1}
    """
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    markers = text.count(FENCE)
    if markers == 0:
        raise ReplyNotJson("the reply holds no JSON and no fenced block")
    if markers == 1:
        raise ReplyNotJson("the reply opens a fenced block and never closes it")
    if markers > 2:
        raise ReplyNotJson("the reply holds more than one fenced block")
    inside = text.split(FENCE)[1]
    _, newline, body = inside.partition("\n")
    try:
        return json.loads(body.strip() if newline else "")
    except json.JSONDecodeError as e:
        raise ReplyNotJson(f"the reply's fenced block is not JSON: {e}") from e
