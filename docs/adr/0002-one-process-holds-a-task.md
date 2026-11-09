---
search:
  exclude: true
---

# One process holds a task

SQLite on a network file system is safe only with one process at a time, and `flock` there
is unreliable. A pass resumes by being rerun, often on another cluster node after a crash, so
a crashed holder must not lock the task for good.

**A holder file, made only if absent.** Opening a task creates `state.holder` beside the
state file with `O_CREAT | O_EXCL`, writing the user, process ID, host and start time. A
second opener finds it and is refused with those named. Closing the task, or a normal exit,
removes it.

**A heartbeat decides when a holder is gone.** A daemon thread touches the holder file every
20 seconds. A holder counts as gone when its process no longer exists, if it ran on this host,
or when its file has gone 120 seconds untouched, on any host. The next opener then deletes it
and takes over.

**A holder that was taken over stops writing.** Every write first checks that the holder file
is still the one this process made, by device and inode, and raises `TaskInUse` if not. A pass
frozen past the limit, then resumed, cannot write beside the process that took over.

## Consequences

- After a crash on another host, a rerun is refused for up to two minutes.
- The age is the file's modification time against the opener's clock, so hosts must agree on
  the time to well within two minutes.
- The heartbeat waits on real time, outside the clock seam. It never delays a test, since
  closing the task wakes it at once.
- Two openers that judge one dead holder gone at the same instant can both take over. The
  window is milliseconds after a crash, and is accepted.
