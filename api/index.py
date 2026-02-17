"""
Vercel Serverless Entry Point for SAMA 2.0
Wraps the FastAPI app for Vercel deployment
"""

import sys
import os
from pathlib import Path

# Add parent directory to path for imports
root_dir = str(Path(__file__).parent.parent)
sys.path.insert(0, root_dir)
os.chdir(root_dir)

# Import the FastAPI app
from main import app

# Export for Vercel
handler = app
