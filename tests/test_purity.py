import subprocess
import sys
from importlib.metadata import requires

#: The gateway's packages, by import name and by distribution name.
GATEWAY = {"litellm": "litellm", "typesafe_sdk": "typesafe-sdk", "psycopg": "psycopg"}


def test_importing_the_library_loads_none_of_the_gateways_packages() -> None:
    code = (
        "import sys, labllm, labllm.testing\n"
        f"print(sorted(set({sorted(GATEWAY)!r}) & set(sys.modules)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_the_plain_install_carries_none_of_the_gateways_packages() -> None:
    plain = [r for r in requires("liulab-llms") or [] if "extra ==" not in r]
    assert not [r for r in plain if r.startswith(tuple(GATEWAY.values()))]
