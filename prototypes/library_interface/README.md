# PROTOTYPE: the library's interface

Throwaway. It answers one question: how a repo declares a unit kind and starts a pass, and how
code with no units calls models through the call layer alone. Never merged.

```bash
cd prototypes/library_interface
pixi run python bounding.py       # seqforge's bounding kind: three passes, the review queue, two reviews
pixi run python harvest_call.py   # one harvest call through a meter, then a ceiling refusal
```

| File | What it shows |
| --- | --- |
| `labllm_sketch.py` | The interface: call layer, engine, and a fake gateway for tests |
| `bounding.py` | A kind as a declared list of steps and checks, and the command that starts a pass |
| `bounding_as_function.py` | The same kind as one plain function, for comparison (not run) |
| `harvest_call.py` | The call layer with no unit or pass |

Everything lives in memory and the provider is played by `FakeGateway`.
