---
search:
  exclude: true
---

# Where the record is kept

The record lives in PostgreSQL on the gateway VM's data disk, and a bucket holds full dumps
of it. Two choices cost something if reversed.

**The bucket holds copies, not the database.** Mounting a bucket as PostgreSQL's data
directory, with the local disk in front as a cache, would make the bucket the store. It
fails for a database. SkyPilot's `MOUNT` mode has no random writes, which PostgreSQL makes
on every write. Its `MOUNT_CACHED` mode uploads a file only once every process has closed
it; PostgreSQL holds its files open, so the bucket's copy lags without bound, and files
uploaded at different moments never form a database that opens. Each change uploads the
whole file again, and Google says Cloud Storage FUSE "shouldn't be used as the backend for
storing a database." The cost: the disk bounds the record, and a full disk is grown by hand.

**A disaster may lose up to a day.** The newest copy off the disk is the last nightly dump,
and a daily snapshot of the disk is the fast restore. Shipping PostgreSQL's write-ahead log
to the bucket would cut the loss to minutes, at the price of a file per log segment, a
restore that replays them and a drill of its own. A lost day's calls are paid again when
rerun, and their exchanges are gone.

## Consequences

- Every dump is the whole record, so loading any one into a gateway replays its cache
  entries. The month's first dump is kept longer, to undo damage noticed late; the bucket's
  lifecycle file holds how long.
- Old records stay on the disk. If the disk grows past what is cheap to keep, the next step
  is chunk files of old records in the bucket, still read back on a cache hit.
- Nothing promises that an old dump loads into a newer gateway. The schema only adds, so it
  should.
