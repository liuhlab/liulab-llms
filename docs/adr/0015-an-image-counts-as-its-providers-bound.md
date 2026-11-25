---
search:
  exclude: true
---

# An image counts as its provider's bound

The ceiling holds back the most a call can cost before it is sent. Counting an image's data
URL byte by byte priced a 2 MB screenshot at millions of tokens, while a linked image counted
only its URL. Two choices cost something if reversed.

**Each image counts as the most its provider bills for one image.** `IMAGE_TOKENS` in
`labllm.ceiling` holds one bound per provider, the largest over its models and detail levels,
from the provider's own documentation:

- DeepSeek, 1,024: [its vision guide](https://api-docs.deepseek.com/guides/vision) states "an
  upper bound of 1024 tokens per image".
- Anthropic, 4,784: [the Claude vision guide](https://platform.claude.com/docs/en/build-with-claude/vision)
  gives the high-resolution tier, Claude 4.7 and later, at most 4,784 visual tokens an image,
  and the standard tier 1,568.
- OpenAI, 73,800: [its images and vision guide](https://developers.openai.com/api/docs/guides/images-vision)
  rejects an image over 30,000 patches, and its largest patch multiplier is 2.46: 30,000 ×
  2.46. Its largest tile cost is lower, 2,833 + 8 × 5,667 = 48,169 for `gpt-4o-mini`.

The image's URL leaves the byte count, and text keeps one token per byte. A bound from the
image's pixel size was turned down: it needs an image decoder in the gateway, and a linked
image has no pixels to read. The cost: one bound per provider holds back too much for its
cheaper models, most for OpenAI, and a provider that raises its maximum leaves the table too
low until someone raises it too.

**An image for a provider outside the table is refused.** The gateway cannot bound it, so it
answers `Rejected` before any attempt, as for a model with no price. The cost: images to a
new provider wait for someone to read its rule and add one line.

## Consequences

- The fake and the real gateway share the table through `largest_cost`.
- The ceiling counts three kinds of content part, `text`, `image_url` and `file`, and
  refuses a call with any other.
- The rate-limit estimate counts each image at the same bound, and usage corrects it after.
- A provider's new model with a larger image rule needs its bound raised before callers use
  it.
