"""
Vercel Serverless Entry Point for SAMA 2.0
Minimal version to test deployment
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create minimal app
app = FastAPI(title="SAMA 2.0", version="2.0.0")

# CORS - allow all
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "service": "SAMA 2.0",
        "status": "operational",
        "version": "2.0.0-minimal"
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

# Export for Vercel
handler = app
