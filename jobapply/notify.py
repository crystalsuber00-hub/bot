from __future__ import annotations

import os
import shutil
import subprocess

import requests


def alert(title: str, body: str = "") -> None:
    """Tell the user something needs them: phone push (ntfy), macOS banner, and the terminal."""
    print(f"\a*** {title}: {body}")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"{os.environ.get('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')}/{topic}",
                          data=(body or title).encode(), timeout=10,
                          headers={"Title": title, "Priority": "high", "Tags": "briefcase"})
        except requests.RequestException as e:
            print(f"(phone alert failed: {e})")
    if shutil.which("osascript"):
        esc = lambda t: t.replace("\\", "").replace('"', "'")  # noqa: E731
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(body)[:200]}" with title "{esc(title)}"'],
                       check=False)
