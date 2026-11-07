import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "stress.py"


def test_parallel_processes_never_collide():
    output = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode=twinport",
            "--workers=8",
            "--iterations=200",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    result = json.loads(output)
    assert result["allocations"] == 1600
    assert result["collisions"] == 0
