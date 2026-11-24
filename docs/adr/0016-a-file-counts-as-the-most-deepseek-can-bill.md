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

**The request form carries the file inline.** The caller sends OpenAI's file block with a
base64 data URL, so the cache key covers the file's bytes. The gateway uploads it and swaps
in the `file_id`. The cost: the record keeps each form whole, file included.

**The ceiling counts a form with a file at its provider's context length,** 1,048,576 tokens
for DeepSeek, plus 1,000 pages of parsing for each document. A file's text has no smaller
bound before parsing. Each later call with a file holds back the same amount.

**Parsing is paid at its bound, once.** The gateway reuses an upload of the same bytes for a
day, so a file sent in many calls is parsed once, and the first attempt to carry it pays
1,000 pages. The record overstates parsing; it never understates it.

**Text recognition only.** The richer purpose adds figures as images, which DeepSeek
counts against its image limit. Price lists and catalogues are read for text.

**LiteLLM is bypassed for these messages.** It cuts a DeepSeek message with any part but
text and images down to its text, so a message list with a file goes in `extra_body`.

## Consequences

- Only DeepSeek takes files; a file for another provider is refused before any attempt.
- A new file type, or a model with a larger context, needs one line in `labllm.files`.
- The fake gateway checks and reserves for files by the same rules, and charges no parsing.
