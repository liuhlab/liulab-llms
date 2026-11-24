"""Files a chat call carries for DeepSeek to parse: which kinds, how large, and what they cost.

DeepSeek is the only provider that takes them. It parses a PDF, Word or PowerPoint file into
text and turns an Excel file into Markdown; the model reads the result as input. Every limit
here is from DeepSeek's own pages, which
`docs/adr/0016-a-file-counts-as-the-most-deepseek-can-bill.md` cites.

A caller checks a file's size with `max_bytes` before downloading it, then attaches it with
`file_part`. The gateway applies the same rules before it sends anything.

Examples
--------
>>> max_bytes("price list.pdf") // 2**20
100
>>> block = file_part("prices.xlsx", b"...")
>>> block["file"]["filename"]
'prices.xlsx'
"""

import base64
import binascii
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from labllm.errors import Rejected

#: The kind of each file extension DeepSeek parses: a document is parsed page by page and
#: billed per page; a spreadsheet is turned into Markdown for free.
KINDS = {
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".ppt": "document",
    ".pptx": "document",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
}

#: The largest file of each kind DeepSeek takes, in bytes.
MAX_BYTES = {"document": 100 * 2**20, "spreadsheet": 32 * 2**20}

#: The most pages of one document DeepSeek parses. Nothing here counts pages: DeepSeek refuses
#: a longer one, and the gateway rejects the call.
MAX_PAGES = 1000

#: The most files one call may carry.
MAX_FILES = 20

#: What DeepSeek charges to parse one page of a document.
USD_PER_PAGE = 0.225 / 1000

#: The most input tokens a call with a file can be billed: its provider's context length.
CONTEXT_TOKENS = {"deepseek": 2**20}

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def max_bytes(filename: str) -> int:
    """Return the largest file DeepSeek takes under `filename`, by its extension.

    Raises
    ------
    Rejected
        DeepSeek does not parse files with this extension.
    """
    return MAX_BYTES[_kind(filename)]


def file_part(filename: str, data: bytes) -> dict[str, Any]:
    """Return a content block that carries `data` in a chat call's `user` content.

    The block is OpenAI's file block, with the file inline as a base64 data URL.

    Raises
    ------
    Rejected
        DeepSeek does not parse this kind of file, or it is over DeepSeek's size limit.
    """
    _check_size(filename, len(data))
    suffix = PurePosixPath(filename).suffix.lower()
    url = f"data:{_MEDIA_TYPES[suffix]};base64,{base64.b64encode(data).decode()}"
    return {"type": "file", "file": {"filename": filename, "file_data": url}}


@dataclass(frozen=True)
class Attached:
    """A file a chat form carries, read from its content block."""

    filename: str
    kind: str
    data: bytes

    @property
    def digest(self) -> str:
        """The SHA-256 of its kind and bytes: one file uploaded once, whatever its name."""
        return hashlib.sha256(self.kind.encode() + b"\0" + self.data).hexdigest()

    @property
    def most_usd(self) -> float:
        """The most DeepSeek can charge to parse it: a document at its page limit."""
        return MAX_PAGES * USD_PER_PAGE if self.kind == "document" else 0.0


def is_file(part: Any) -> bool:
    """Return whether a content part is a file block."""
    return isinstance(part, dict) and part.get("type") == "file"


def attached(form: dict[str, Any]) -> list[Attached]:
    """Return the files a chat form carries, in order, once each is checked.

    Raises
    ------
    Rejected
        The form has a file for a provider other than DeepSeek, more than `MAX_FILES`, one
        that is not a well-formed file block, one DeepSeek does not parse, or one over its
        size limit.
    """
    if form["route"] != "chat":
        return []
    blocks = [
        p
        for m in form["messages"]
        if isinstance(m, dict) and isinstance(m.get("content"), list)
        for p in m["content"]
        if is_file(p)
    ]
    if not blocks:
        return []
    if form["provider"] not in CONTEXT_TOKENS:
        raise Rejected(f"only DeepSeek takes files, not {form['provider']}")
    if len(blocks) > MAX_FILES:
        raise Rejected(f"a call carries at most {MAX_FILES} files, not {len(blocks)}")
    return [_read(block) for block in blocks]


def _read(block: dict[str, Any]) -> Attached:
    inner = block.get("file")
    name = inner.get("filename") if isinstance(inner, dict) else None
    url = inner.get("file_data") if isinstance(inner, dict) else None
    if not isinstance(name, str) or not isinstance(url, str) or ";base64," not in url:
        raise Rejected("a file block carries a filename and its file as a base64 data URL")
    try:
        data = base64.b64decode(url.split(";base64,", 1)[1], validate=True)
    except (binascii.Error, ValueError) as e:
        raise Rejected(f"the file {name!r} is not valid base64: {e}") from e
    _check_size(name, len(data))
    return Attached(name, _kind(name), data)


def _kind(filename: str) -> str:
    kind = KINDS.get(PurePosixPath(filename).suffix.lower())
    if kind is None:
        raise Rejected(
            f"{filename!r} is not a file type DeepSeek parses: give one of {', '.join(KINDS)}"
        )
    return kind


def _check_size(filename: str, size: int) -> None:
    limit = max_bytes(filename)
    if size > limit:
        raise Rejected(f"{filename!r} has {size} bytes, over DeepSeek's limit of {limit}")
