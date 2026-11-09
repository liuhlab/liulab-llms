# Glossary

The words for model calls through the lab gateway, and for the work run over many units.

## Glossary

### Ceiling

A meter's limit in dollars, required and with no default. The gateway refuses a call whose
largest possible cost (estimated input plus the full output allowance), added to what the
meter has spent and holds for calls in flight, would cross it. A cache hit never counts.
_Avoid_: budget, cap, limit

### Dropped

An ending a person's review gives: the unit is taken out of the work, with a note giving the
reason. No answer is kept. Like an answer from a review, it holds until the unit's inputs tag
changes.
_Avoid_: skipped, excluded

### Exchange

One attempt at a provider, retries each their own, recorded whole in the gateway's record:
the request, the response or error, usage, cost, and the person, repo, meter and tag. A cache
hit adds an exchange with no bodies.
_Avoid_: transcript, log entry

### Gateway

The lab's one service that sends every model call: it holds the provider keys, keeps the
record, replays any request it has answered, and enforces each meter's ceiling. `Gateway` is
also the library's client for it.
_Avoid_: proxy

### Held

A unit whose inputs are not there yet: its kind's loader raised `Held`, and the reason is
recorded. Not an ending, so it leaves the unit's current answer as it was.
_Avoid_: skipped, pending

### Inputs tag

A string the repo's loader returns with a unit's inputs, stamped on its answer. The engine
only compares it, so the repo chooses how fine it is.
_Avoid_: hash, checksum

### Meter

A group of calls under one ceiling, with its own identity on the gateway. Its label is for
people, so two meters with one label never share a ceiling. A pass opens one meter.
_Avoid_: session, budget

### Pass

One run of a unit kind over the keys it is given, in that order, under one meter. It records
each unit's outcome and returns counts per ending. Rerunning the same command starts a new
pass whose finished calls replay free, which is how a stopped pass resumes.
_Avoid_: run, batch, job, round

### Record

The gateway's one table of every exchange, which is also its response cache: the cache
entry for a request is its first successful exchange not retired. A bad entry is retired by
hand and stays in the record. Each meter's spend is rebuilt from it after a restart.
_Avoid_: log, transcript, cache table

### Refused

An ending: the kind's code refused the unit with a message. No answer is kept.
_Avoid_: rejected, failed

### Request form

What decides a call's answer, as `labllm.wire` builds it: provider, exact model name,
settings, the messages or Jev's description and questions, and the output schema. Its hash
is the cache key; labels, the tag and the ceiling are never in it.
_Avoid_: payload, prompt

### Review

One person's recorded decision on one unit: an answer, or a drop. It is a line in its kind's
review file, made for the unit's inputs tag of that moment, and outranks the models' answer
while the tag is unchanged. Of a unit's reviews for one tag, the latest-timed wins.
_Avoid_: correction, label, annotation

### Review queue

Every unit whose current answer is unsettled or refused, in key order: the units a person
must look at.
_Avoid_: inbox, backlog, triage list

### Settled

An ending: the kind returned an answer and every check passed, or a person overrode a failed
check with a reason, or its code alone settled the unit.
_Avoid_: done, accepted

### Stamp

What produced an answer, recorded with it: the kind and its version, the model for each
role, the inputs tag, the pass, and the calling repo's and liulab-llms's code versions.
_Avoid_: provenance, metadata

### Task

One body of work's state: one SQLite file in a folder the calling command names, held by one
process at a time. It keeps every pass and each unit's outcome, answer, stamp and check
results per pass. Deleting the file is safe: rerunning rebuilds the models' answers from the
cache.
_Avoid_: project, database

### Unfinished

A unit that reached no ending in a pass: its kind's code raised an error, its answer did not
fit the answer schema, or the pass stopped. The reason is recorded. Not an ending, so it
leaves the unit's current answer as it was.
_Avoid_: failed, errored

### Unit

One independent piece of work, named by a string key, such as one sequencing Series. Its
current answer is its outcome in the latest pass that reached an ending.
_Avoid_: item, row, sample

### Unit kind

A repo's declaration of one kind of unit: a name, a hand-set version, roles, a loader, one
plain function that does the work, an answer schema and answer checks.
_Avoid_: step list, pipeline

### Unsettled

An ending: the kind returned an answer that fits its schema, but a check failed. The answer
is kept with each failed check's message.
_Avoid_: doubtful, partial
