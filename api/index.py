"""
Vercel Serverless Entry Point for SAMA 2.0
Wraps the FastAPI app for Vercel deployment
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
