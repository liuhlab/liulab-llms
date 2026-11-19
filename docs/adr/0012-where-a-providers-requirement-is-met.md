---
search:
  exclude: true
---

# Where a provider's requirement is met

DeepSeek takes no schema, so the gateway's provider mapping, `labllm.server.chat.request`,
asks it for JSON mode, and JSON mode is refused unless a message says "json". Three choices
cost something if reversed.

**The mapping meets what its own request requires.** The caller wrote `schema=` and the
mapping chose JSON mode, so the mapping adds the instruction JSON mode needs. The request
form stays the same for every provider, and no cache key changes. A rule in the docs would
put one provider's rule in every caller's prompt, and the fake gateway plays no provider's
rules, so a caller would first meet it in a live pass. A rule in the client's form would put
provider knowledge in the plain install, and two client versions would build two forms for
one call. The cost: the provider is sent text the form does not hold. A change to that text
changes the request under the same key, so answers cached under the old text still replay.

**Only a form whose messages never say "json", in any letter case, gets the instruction.**
Every request that worked before reaches the provider unchanged. The cost: a form that says
"json" is still sent no schema, so the shape of its answer depends on the caller's prompt
alone.

**The instruction carries the schema.** It is `Answer in JSON that fits this schema:`, a
line break, and the form's schema as compact JSON with its keys in the order written. It
follows the form's own system text in the one system message, or is a new first message,
because nothing shows that DeepSeek accepts two system messages. The word alone would get a
paid answer of no agreed shape, where the call used to be refused for free.

## Consequences

- A call's reservation counts the form as sent. A provider that sends more input than its
  form holds returns that form from `as_sent(form)`, and the server reserves on it. The fake
  gateway reserves on the form itself, so for these calls it holds less than the real one.
- The rate limit's estimate still reads the form; the provider's input count corrects it.
- The condition is that the mapping chose JSON mode, which today means DeepSeek.
- How well DeepSeek fits a schema given as text is unknown: finding out needs a paid call.
