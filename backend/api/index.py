"""FastAPI entry point for Vercel's Python serverless runtime (Services mode).

With the service root set to 'backend/', Vercel expects the ASGI app at
backend/api/index.py.  This shim adds the service root to sys.path so
that 'app' package resolves correctly with its relative imports.

All application logic lives in backend/app/ — nothing changes there.
The VERCEL env var is injected automatically by Vercel's runtime and
triggers the /tmp cache path and synchronous refresh in app/main.py.
"""
from __future__ import annotations

import os
import sys

# Service root is backend/; adding it to path lets 'from app.main import app'
# resolve the package and its intra-package relative imports correctly.
_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVICE_ROOT)

from app.main import app  # noqa: E402 — path setup must precede this import

__all__ = ["app"]
