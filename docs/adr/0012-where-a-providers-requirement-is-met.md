---
search:
  exclude: true
---

# Where a provider's requirement is met

DeepSeek has no native schema response, but it takes a strict function call with thinking
disabled. The gateway's provider mapping, `labllm.server.chat.request`, must bridge that
difference without making every caller know a provider's rules.

**The mapping turns a DeepSeek schema into one forced function call.** The function is named
`answer`; its parameters are the form's schema, marked strict. The function's JSON arguments
become the call's text. Text and image content blocks reach the provider unchanged. Other
providers keep native schema output. The request form and its cache key therefore stay the
same for every provider, and the plain client needs no provider knowledge. The cost is
provider-specific response handling in the gateway.

**A DeepSeek schema requires thinking off.** A form that explicitly asks for thinking is
rejected before a paid call; one that leaves it unset or false is sent with thinking disabled.
Combining both settings would produce a request DeepSeek refuses. The cost is that a caller
cannot request reasoning and a schema together.

**The gateway skips LiteLLM's optional MCP handling for this call.** LiteLLM imports its MCP
module whenever tools are present, but MCP is not a gateway dependency and this function is
not an MCP tool. The cost is reliance on LiteLLM's private skip setting, pinned with its
version and covered by the provider-mapping test.

## Consequences

- The reservation counts the original form: the mapping adds no prompt text.
- A call without a schema keeps its messages and has no response format or tools.
- The fake gateway does not imitate a provider's mapping; server tests cover the real request
  and function-argument response. It refuses the forms the mapping refuses, through the one
  check both call, `labllm.wire.check_chat`.
- Existing cache keys do not change when the provider mapping changes.
