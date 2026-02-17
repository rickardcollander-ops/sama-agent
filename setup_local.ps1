# SAMA 2.0 Local Setup (Without Docker)
# For Windows users without Docker Desktop

Write-Host "🚀 SAMA 2.0 Local Setup (No Docker)" -ForegroundColor Cyan

# Check if Python is installed
Write-Host "`n1️⃣ Checking Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✅ Python installed: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python not found. Install Python 3.12+ from https://www.python.org/" -ForegroundColor Red
    exit 1
}

# Create virtual environment
Write-Host "`n2️⃣ Creating virtual environment..." -ForegroundColor Yellow
if (!(Test-Path "venv")) {
    python -m venv venv
    Write-Host "✅ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "✅ Virtual environment already exists" -ForegroundColor Green
}

# Activate virtual environment
Write-Host "`n3️⃣ Activating virtual environment..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1

# Install dependencies
Write-Host "`n4️⃣ Installing dependencies..." -ForegroundColor Yellow
pip install anthropic fastapi uvicorn pydantic pydantic-settings httpx python-dotenv

Write-Host "`n✅ Basic setup complete!" -ForegroundColor Green
Write-Host "`n⚠️  NOTE: Running without Redis and PostgreSQL" -ForegroundColor Yellow
Write-Host "Some features will be limited:" -ForegroundColor Yellow
Write-Host "  - No event bus (agent communication)" -ForegroundColor Yellow
Write-Host "  - No database persistence" -ForegroundColor Yellow
Write-Host "  - Agents will work but won't save data" -ForegroundColor Yellow

Write-Host "`n📋 Next steps:" -ForegroundColor Cyan
Write-Host "1. Create .env.local and add ANTHROPIC_API_KEY"
Write-Host "2. Run: uvicorn main:app --reload"
Write-Host "3. Visit: http://localhost:8000/docs"

Write-Host "`nFor full functionality, install Docker Desktop" -ForegroundColor Yellow
