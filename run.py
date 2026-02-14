"""Start the Placement Team web dashboard.

Usage:
    python run.py

Server runs at http://localhost:8000 (or next free port 8001, 8002, ... if 8000 is in use).
Optionally opens the browser automatically.
"""

import socket
import sys
import webbrowser
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _free_port(start: int = 8000, end: int = 8010) -> int:
    """Return the first port in [start, end] that is free to bind."""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start  # fallback, uvicorn will raise if still in use


if __name__ == "__main__":
    import uvicorn
    host = "127.0.0.1"
    port = _free_port()
    url = f"http://{host}:{port}"
    print(f"Starting server at {url}")
    print("Press Ctrl+C to stop")
    webbrowser.open(url)
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=True,
    )
