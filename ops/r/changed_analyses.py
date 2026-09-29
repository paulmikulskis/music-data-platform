"""List analyses to check; an unavailable push base checks every analysis."""

import os
import subprocess
from pathlib import Path


def changed_analyses():
    base = os.environ.get("MDP_R_BASE", "")
    valid_base = bool(base) and subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    # First pushes use an all-zero SHA; manual runs may have no base at all.
    # Checking every tracked analysis also covers bases absent from a shallow clone.
    command = (
        ["git", "diff", "--name-only", "-z", base]
        if valid_base else ["git", "ls-files", "-z", "analyses/"]
    )
    changed = subprocess.check_output(command).decode().split("\0")
    changed += subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "analyses/"]
    ).decode().split("\0")
    return sorted({
        str(Path(*Path(path).parts[:3]))
        for path in changed
        if path.startswith("analyses/") and len(Path(path).parts) > 3
        and Path(*Path(path).parts[:3]).is_dir()
    })


if __name__ == "__main__":
    for folder in changed_analyses():
        print(folder)
