---
search:
  exclude: true
---

# An agent's loop runs in the client

An agent calls a model in a loop and runs one of the caller's tools whenever the model asks
for it. Three choices cost something if reversed.

**The loop runs in the client, one cached chat call a step.** A tool is the caller's own
function, such as a query of its database, so only the caller can run it. Each step is a chat
call whose request form holds the agent's messages so far. A rerun replays every step free
while each tool gives the same result again; a changed result misses the cache from that
step on. Running the loop in the gateway was turned down: the gateway would have to call
back into the caller. The cost: each step sends the whole conversation again, and its input
is paid each time.

**Tools and tool calls travel in OpenAI's chat format.** A form with tools gains `tools`,
each `{"name", "description", "parameters"}`, after `schema`; a form without tools keeps its
key. A step that asked for tools adds an assistant message with `tool_calls`, then one
`tool` message per result. A reply carries the calls as `tool_calls`, with the provider's
ids and argument text unchanged, and the record keeps them beside the text. The cost:
renaming or reshaping any of these changes the key of every agent step.

**Only DeepSeek gets tools.** The caller's tools go to DeepSeek as strict functions beside
the schema's `answer`, with thinking off, and the model must call one of them, as in
[0012](0012-where-a-providers-requirement-is-met.md). A call to `answer` ends the agent. The
gateway refuses tools for any other provider before a paid call. The cost: another provider
waits for its own mapping and test.

## Consequences

- The fake gateway plays a model asking for tools when its provider function returns
  `ToolCall`s, and numbers their ids itself.
- A gateway older than tools sends the form without them, and caches that answer under the
  form's key. Agents wait for the upgraded gateway.
- The ceiling counts the tools' definitions as input.
- `StepLimitReached` and `UnknownTool` end only their agent. Neither is a `Stopped`, so a
  pass goes on.
