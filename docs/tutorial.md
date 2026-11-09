# Write your first unit kind

In this tutorial we build a small unit kind that names the species in a sample's description.
We test it on a fake gateway, run it, send the samples it could not settle to a stronger
model, and review the two that are left.

A **unit** is one independent piece of work, named by a string key: here,
one sample. A **unit kind** says how to do the work for any unit of that kind. A **pass** runs
a kind over a list of units and records how each one ended.

## Before you start

You need a repo with pixi and liulab-llms in its environment. Steps 1 to 5 run on the fake
gateway in `labllm.testing`, which needs no network and costs nothing. Only step 6 calls real
models, and for that you need a tunnel to the lab gateway: see
[Reach the lab gateway](reach-the-gateway.md).

## 1. Declare the kind

Make a file named `species.py`, and declare the kind in it:

```python
--8<--
tests/tutorial/species.py:kind
--8<--
```

The kind has these parts:

- `load` returns a unit's inputs and its **inputs tag**, a string that changes when the inputs
  change. Here the tag is the description itself. A real `load` reads your data. If an input
  is not there yet, it raises `Held`, and the pass keeps that unit's current answer as it was.
- `name_species` does the work for one unit, `u`. It asks the model in the `reader` role and
  returns the answer. For an empty description it calls `u.refuse`, which ends the unit with
  no model call.
- `ANSWER` is a JSON Schema the answer must fit. An answer that does not fit is not kept.
- `named_in_text` is an answer check. It returns `None` when the answer passes, or a message
  that says what is wrong.
- `version` is yours to set. Raise it when you change the kind. Every answer records the
  version that made it.

Each unit ends in one of four ways, called its **ending**:

- **settled**: the unit has an answer, and every check passed.
- **unsettled**: the unit has an answer, but a check failed. The answer is kept with the
  check's message.
- **refused**: the kind's code refused the unit. No answer is kept.
- **dropped**: a person took the unit out of the work, with a note. Only a review gives it.

## 2. Write the command

Add the models and a command to `species.py`:

```python
--8<--
tests/tutorial/species.py:command
--8<--
```

The command runs one of two passes:

- `first_pass` runs the cheaper model over every sample.
- `stronger_pass` runs the stronger model over only the samples whose current ending is
  unsettled or refused. Sending a unit to a stronger model in a narrower pass like this is
  called moving it up the ladder.

`Task` keeps the work's state in one file in the folder `work/species`: every pass, and each
unit's answer and checks. Only one process at a time can open a task. Each pass sends its calls through a **meter** with a **ceiling**:
the most the pass may spend, in US dollars. The gateway refuses any call that could take the
meter past it. `main` returns 1 if the pass stopped early, such as at its ceiling, and 0
otherwise.

## 3. Test the first pass

Make `test_species.py` beside `species.py`. Start it with a fake gateway:

```python
--8<--
tests/tutorial/test_species.py:fake
--8<--
```

`play_the_models` plays both models. It gets the request a model would get, and returns the
reply's text. Our fake cheaper model gives the zebrafish's Latin name, so the check fails for
that sample. The prices are made up. The fixture also moves each test into its own empty
folder, because the command keeps its files in the folder it runs in.

Now add a test of the first pass:

```python
--8<--
tests/tutorial/test_species.py:first
--8<--
```

Run it:

```bash
pixi run pytest test_species.py
```

You see `1 passed`. The test shows what the command printed: two samples settled, two
unsettled and one refused. `s3` is unsettled because "Danio rerio" is not in its
description. `s5` is unsettled because "mouse" is not in "C57BL/6J liver". `s4` is refused,
since its description is empty.

## 4. Move up the ladder

Add a test of the stronger pass:

```python
--8<--
tests/tutorial/test_species.py:stronger
--8<--
```

Run `pixi run pytest test_species.py` again. You see `2 passed`.

The stronger pass took `s3`, `s4` and `s5`, the three units the first pass did not settle.
The stronger model names the zebrafish, so `s3` settles. A unit's current answer is the one
from its latest pass that reached an ending, so `s3`'s answer is now the stronger model's.
`s5` stays unsettled, because its description never says "mouse". `s4` is refused again
before any model call, so only `s3` and `s5` reached the stronger model.

## 5. Review what is left

The **review queue** holds every unit whose current answer is unsettled or refused: the units
a person must look at. A **review** is one person's decision on one unit, either an answer or
a drop. Add a test that reviews both units left:

```python
--8<--
tests/tutorial/test_species.py:review
--8<--
```

Run `pixi run pytest test_species.py` again. You see `3 passed`. The test does four things:

1. It reads the queue with `task.review_queue`. Each entry has the unit's key and ending, the
   failed checks in `failed`, and every pass's answer in `outcomes`.
2. It drops `s4` with `task.save_review`, with a note giving the reason.
3. It saves the answer "mouse" for `s5` with `Reviews`. `named_in_text` fails, so the first save
   saves nothing and raises `ChecksFailed`, whose `failed` lists the failed checks. The second save overrides the check with a
   reason: C57BL/6J is a mouse strain.
4. It opens the task again. The queue is empty, `s5` is settled, and `s4` is dropped.

`Reviews` needs no task. It writes only to the reviews folder, so you can save reviews while
a pass holds the task. Give it the entry's `inputs_tag`, the tag of the inputs the person
reviewed. If the inputs changed since, the save raises `ValueError`: run a pass, then review
its new answer. `task.save_review` finds the tag itself.

A review outranks the models' answer until the unit's inputs tag changes. Then the unit goes
back through the models.

## 6. Run it on the lab's models

Open your tunnel to the gateway and set `LABLLM_GATEWAY`, as
[Reach the lab gateway](reach-the-gateway.md) shows. Then run the first pass:

```bash
pixi run python species.py
```

It prints the count of each ending. Real models may not answer as the fake did, so your
counts can differ from the test's. Then run the stronger pass:

```bash
pixi run python species.py --stronger
```

Run the same command again and the gateway answers every call it has seen from its cache, at
no cost. That is also how you resume a pass that stopped: run the same command again.

To review the units left, run the lines of step 5 in a notebook or a script. Use your own
`Gateway`, not the fake one, and your own name for `who`.

## 7. Keep the reviews in git

Reviews live in the reviews folder, one file for each kind, such as `reviews/species.jsonl`.
Commit that folder. Deleting `work/species` loses no reviews, and a fresh pass replays the
models' answers from the gateway's cache.

Two people may add reviews on two branches at once. Tell git to keep both sides' lines when
it merges, by adding this line to your repo's `.gitattributes`, with your folder's path:

```text
reviews/*.jsonl merge=union
```

When two lines review the same unit, the one saved later wins.
