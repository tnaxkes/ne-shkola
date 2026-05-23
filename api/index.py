"""
Vercel serverless entry point.
All requests are routed here via vercel.json.
"""
import sys
from pathlib import Path

# Make sure the project root is on the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app  # noqa: F401  (Vercel picks up the ASGI app by name)
