"""
Vercel serverless entrypoint.

Vercel's Python runtime looks for a module-level variable named `app` that is
an ASGI/WSGI application and serves it. We simply re-export the FastAPI app
defined in webapp.py.

The sys.path tweak lets this file (which lives in api/) import the modules that
sit one directory up (webapp.py, agent_core.py, spotify_tools.py).
"""

import os
import sys

# Make the project root (one level up from api/) importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import app  # noqa: E402,F401  — Vercel serves this `app`
