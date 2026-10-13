"""macOS notification (best effort, never raises). Unattended runs need a nudge when they finish, park or need a human."""
import json
import subprocess
import sys


def send(title, message):
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
