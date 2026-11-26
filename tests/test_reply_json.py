"""Reading the JSON out of a chat reply that was sent with no schema."""

import pytest

from labllm import ReplyNotJson, reply_json

FENCE = "```"

#: Each reply shape, and the value it reads as or the case its error names.
SHAPES = [
    ('{"a": 1}', {"a": 1}),
    ("[1, 2]", [1, 2]),
    ('\n  {"a": [true, null]}\n\n', {"a": [True, None]}),
    (f'{FENCE}json\n{{"a": 1}}\n{FENCE}', {"a": 1}),
    (f"{FENCE}\n[1, 2]\n{FENCE}\n", [1, 2]),
    (f'Here it is:\n{FENCE}json\n{{"a": 1}}\n{FENCE}\nHope that helps.', {"a": 1}),
    ('The answer is {"a": 1}, as asked.', "no JSON and no fenced block"),
    (f'{FENCE}json\n{{"a": 1}}\n{FENCE}\nor\n{FENCE}\n{{"a": 2}}\n{FENCE}', "more than one"),
    (f'{FENCE}json\n{{"a": 1}}\n', "never closes"),
    (f"{FENCE}json\nthe answer is yes\n{FENCE}", "fenced block is not JSON"),
]


@pytest.mark.parametrize(("text", "expected"), SHAPES)
def test_a_reply_reads_as_its_whole_text_or_its_one_fenced_block(
    text: str, expected: object
) -> None:
    if isinstance(expected, str):
        with pytest.raises(ReplyNotJson, match=expected) as raised:
            reply_json(text)
        assert isinstance(raised.value, ValueError)
    else:
        assert reply_json(text) == expected
