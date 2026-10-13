"""macOS notification (best effort, never raises). Unattended runs need a nudge when they finish, park or need a human."""
import json
import subprocess
import sys


def send(title, message):
    if sys.platform != "darwin":
        return
    script = (f"display notification {json.dumps(message, ensure_ascii=False)} "
              f"with title {json.dumps(title, ensure_ascii=False)}")
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
