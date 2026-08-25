"""Running code the model wrote, without handing over the keys to the house.

The agents in this project write Python and then run it. On a laptop that is
fine. Exposed on the internet it is not: a stranger types a task, the model
writes code to match, and that code executes on the server. Someone will find
the endpoint — these get scanned within hours of going up.

The defence here is deliberately modest and worth stating plainly.

WHAT THIS STOPS
  - Reading the service's secrets. The child process gets an environment built
    from scratch, so GOOGLE_API_KEY and friends are simply not there to read.
  - Touching anything the service owns. Each run gets its own temp directory
    and cannot see the rest of the filesystem by relative path; the directory
    is deleted afterwards.
  - Running forever, or filling the disk. Hard timeout, output truncated,
    file sizes capped.

WHAT THIS DOES NOT STOP
  - Outbound network calls. The code can still reach the internet, which means
    a determined visitor could use this as a proxy. Blocking egress needs
    kernel-level controls a normal Cloud Run container does not get.
  - Burning CPU for the length of the timeout.

That trade is acceptable here because the container is ephemeral, holds nothing
of value once the environment is scrubbed, and is capped at a few instances.
The strong version of this is a throwaway container per execution (Modal
Sandboxes, E2B); this is the honest, cheap version, and the README says so.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 4000
MAX_FILE_BYTES = 100_000
MAX_FILES = 12


class Workspace:
    """A throwaway directory that the agents may write to and run from.

    Every path the agents pass in is resolved inside this directory. A model
    that decides to write to `../../etc/passwd` or `/app/app.py` gets its path
    rewritten to something harmless rather than an exception it will then try
    to work around.
    """

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="softcomp-"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def resolve(self, filepath: str) -> Path:
        """Map any path the model asks for onto a safe path inside the workspace."""
        # Keep the basename only: no directories, no traversal, no absolutes.
        name = os.path.basename(str(filepath).strip().replace("\\", "/")) or "main.py"
        if name in (".", ".."):
            name = "main.py"
        return self.root / name

    def files(self) -> dict:
        return {
            p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(self.root.iterdir())
            if p.is_file()
        }

    # ---------- the three tools, as plain methods ----------

    def read_file(self, filepath: str) -> str:
        path = self.resolve(filepath)
        if not path.exists():
            return f"Error: {path.name} does not exist yet."
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]

    def write_file(self, filepath: str, content: str) -> str:
        path = self.resolve(filepath)
        if len(self.files()) >= MAX_FILES and not path.exists():
            return f"Error: too many files, {MAX_FILES} is the limit."
        text = (content or "")[:MAX_FILE_BYTES]
        path.write_text(text, encoding="utf-8")
        return f"Wrote {len(text)} characters to {path.name}."

    def run_python(self, filepath: str) -> str:
        path = self.resolve(filepath)
        if not path.exists():
            return f"Error: {path.name} does not exist, so there is nothing to run."

        try:
            result = subprocess.run(
                [sys.executable, "-I", path.name],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                # The whole point: a fresh environment, so nothing of ours leaks
                # in. PATH and a temp dir are all it gets. -I above also keeps
                # the interpreter from reading user site-packages or PYTHON*
                # variables.
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(self.root),
                    "TMPDIR": str(self.root),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        except subprocess.TimeoutExpired:
            return (f"Error: still running after {TIMEOUT_SECONDS} seconds, so it was "
                    f"stopped. Check for an infinite loop.")
        except Exception as exc:
            return f"Error: could not run it — {type(exc).__name__}: {exc}"

        stdout = (result.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (result.stderr or "")[:MAX_OUTPUT_CHARS]

        if result.returncode == 0:
            return f"Success! Output:\n{stdout}" if stdout.strip() else "Success! It ran with no output."
        return f"Error occurred:\n{stderr or stdout or f'exit code {result.returncode}'}"
