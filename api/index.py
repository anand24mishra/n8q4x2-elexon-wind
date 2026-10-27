"""Vercel serverless entry point for the FastAPI backend.

Adds the repository's backend/ directory to sys.path so the relative-import
'app' package (app.main, app.domain, app.elexon) resolves correctly, then
exposes the FastAPI ASGI application object for Vercel's Python runtime.

This file is intentionally minimal.  All application logic remains in
backend/app/ so that the Docker Compose setup is unaffected.
"""
from __future__ import annotations

import os
import sys

# Resolve the backend package regardless of where Vercel invokes this file.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

from app.main import app  # noqa: E402 — path setup must precede this import

__all__ = ["app"]
