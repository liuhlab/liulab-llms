---
search:
  exclude: true
---

# A file counts as the most DeepSeek can bill

A chat call can carry a PDF, Word, PowerPoint or Excel file for DeepSeek to parse on its
side. [Its file parsing guide](https://api-docs.deepseek.com/guides/document_parsing) sets
the rules: a document is uploaded to `POST /files` with purpose `text_recognition`, sent
before the file, and is billed per page, at most 1,000 pages and 100 MiB; a spreadsheet
uploads as `user_data`, free, at most 32 MiB. A chat message then names the file by
`file_id`. [Its price page](https://api-docs.deepseek.com/quick_start/pricing) charges 0.225
USD per 1,000 pages. Its upload reply carries no page count.

**A PDF counts at its pages,** as pypdf counts them. Word, PowerPoint, and a PDF pypdf cannot
open count at 1,000 pages: their pages are known only once rendered. The cost: pypdf joins
the `gateway` extra, and without it the fake counts every PDF at 1,000.

**The request form carries the file inline,** as OpenAI's file block with a base64 data URL,
so the cache key covers the file's bytes. The gateway uploads it and swaps in the `file_id`.
The record keeps each file as its name and SHA-256, so a tool loop that resends a file does
not store it again. The cost: the client sends the whole file on every call.

**The ceiling counts a form with a file at its provider's context length,** 1,048,576 tokens
for DeepSeek, plus each document's parsing. A file's text has no smaller bound before
parsing.

**Parsing is paid once, by the attempt that uploads,** even when its chat then fails. An
upload of the same bytes is reused for most of a day. The cost: the record overstates a short Word file's
parsing, and its meter shrinks by that much.

**Text recognition only.** The richer purpose adds figures as images, which count against
DeepSeek's image limit. The cost: a price shown only in a picture is not read.

**LiteLLM is bypassed for these messages.** It cuts a DeepSeek message with any part but
text and images down to its text, so a message list with a file goes in `extra_body`. The
cost: LiteLLM's other message rewriting is skipped, such as filling a missing
`reasoning_content` on an earlier assistant message when thinking is on.

## Consequences

- Only DeepSeek takes files; a file for another provider is refused before any attempt.
- A new file type needs its entry in `KINDS` and its media type in `labllm.files`.
- The fake gateway checks and reserves for files by the same rules, and charges no parsing.
