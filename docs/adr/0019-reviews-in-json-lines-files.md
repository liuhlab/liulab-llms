---
search:
  exclude: true
---

# Reviews in JSON Lines files

A person's reviews live outside the state file, one JSON Lines file per kind, `<kind>.jsonl`,
in the folder the repo names. They survive deleting the state file and go into the repo's git.

**Saving needs no task.** `Reviews(folder).save` never opens the state file, so it works while
another process holds the task. It stamps the inputs tag of the answer reviewed, which the
caller passes from the queue entry; `Task.save_review` reads it from the unit's current
outcome and makes the same call. Both run the answer checks on the inputs `kind.load(key)`
returns, and refuse the save when their tag differs: the inputs changed since that answer,
so a pass must run again first. `Held` from `load` refuses it too.

**One write per line.** A save opens the file to append and writes the whole line in one
`write`, then syncs it, so two processes saving at once on a local disk never split a line.
If the file does not end in a newline, as a crash during a save leaves it, the save writes one
first. A reader skips a last line with no newline in silence, and skips any other line that
is not a review with a warning.

**Which review holds.** A unit's current answer is its outcome in the latest pass that reached
an ending. A review outranks it when made for that outcome's inputs tag, so unfinished and
held outcomes change nothing here either. Of the reviews for that tag, the latest by `when`
wins, and a tie goes to the later line. Time, not file order, because a union merge orders
the lines by who merged: Ana merging Ben's branch and Ben merging Ana's leave the same lines
in two orders. A review for another tag stays in the file and holds again if that tag returns.

## Consequences

- Two people reviewing one unit within the gap between their clocks may see the earlier
  review win.
- Two hosts appending to one file on a network file system can interleave lines; people
  review in their own clones, joined by git.
- `accepted_pass` is recorded as given, not checked, since checking needs the state file.
- `keys`, `current` and `review_queue` read the file again only when its size or
  modification time has changed.
