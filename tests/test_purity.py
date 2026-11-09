import subprocess
import sys


def test_importing_the_library_loads_no_litellm() -> None:
    code = "import sys, labllm, labllm.testing; print('litellm' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
