# Quick test script for SAMA 2.0 setup (Windows PowerShell)

Write-Host "🧪 Testing SAMA 2.0 Setup..." -ForegroundColor Cyan

# Test 1: Check if Redis is running
Write-Host "`n1️⃣ Testing Redis connection..." -ForegroundColor Yellow
$redisRunning = docker ps | Select-String "successifier-redis"
if ($redisRunning) {
    Write-Host "✅ Redis is running" -ForegroundColor Green
} else {
    Write-Host "❌ Redis is not running. Start with: cd ..\shared && docker-compose up -d" -ForegroundColor Red
    exit 1
}

# Test 2: Check if PostgreSQL is running
Write-Host "`n2️⃣ Testing PostgreSQL connection..." -ForegroundColor Yellow
$postgresRunning = docker ps | Select-String "successifier-postgres"
if ($postgresRunning) {
    Write-Host "✅ PostgreSQL is running" -ForegroundColor Green
} else {
    Write-Host "❌ PostgreSQL is not running. Start with: cd ..\shared && docker-compose up -d" -ForegroundColor Red
    exit 1
}

# Test 3: Check Python dependencies
Write-Host "`n3️⃣ Checking Python dependencies..." -ForegroundColor Yellow
try {
    python -c "import fastapi, anthropic, sqlalchemy" 2>$null
    Write-Host "✅ Python dependencies installed" -ForegroundColor Green
} catch {
    Write-Host "❌ Python dependencies missing. Install with: pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

# Test 4: Check environment variables
Write-Host "`n4️⃣ Checking environment variables..." -ForegroundColor Yellow
if (Test-Path .env.local) {
    Write-Host "✅ .env.local exists" -ForegroundColor Green
} else {
    Write-Host "⚠️  .env.local not found. Copy from .env.example and configure" -ForegroundColor Yellow
}

Write-Host "`n✅ All checks passed! Ready to run SAMA 2.0" -ForegroundColor Green
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host "1. python setup.py          # Initialize database and keywords"
Write-Host "2. uvicorn main:app --reload # Start SAMA"
Write-Host "3. Visit http://localhost:8000/docs"
