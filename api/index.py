"""Entry point for Vercel: the same FastAPI app as `uvicorn app.server:app`.

Vercel's Python runtime serves the ASGI `app` found here; vercel.json routes every path to it
and bundles the core (submission/), the interface (app/) and the knowledge base (data/).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import app  # noqa: E402,F401
