#!/bin/bash
# Quick test script for SAMA 2.0 setup

echo "🧪 Testing SAMA 2.0 Setup..."

# Test 1: Check if Redis is running
echo -e "\n1️⃣ Testing Redis connection..."
if docker ps | grep -q successifier-redis; then
    echo "✅ Redis is running"
else
    echo "❌ Redis is not running. Start with: cd ../shared && docker-compose up -d"
    exit 1
fi

# Test 2: Check if PostgreSQL is running
echo -e "\n2️⃣ Testing PostgreSQL connection..."
if docker ps | grep -q successifier-postgres; then
    echo "✅ PostgreSQL is running"
else
    echo "❌ PostgreSQL is not running. Start with: cd ../shared && docker-compose up -d"
    exit 1
fi

# Test 3: Check Python dependencies
echo -e "\n3️⃣ Checking Python dependencies..."
if python -c "import fastapi, anthropic, sqlalchemy" 2>/dev/null; then
    echo "✅ Python dependencies installed"
else
    echo "❌ Python dependencies missing. Install with: pip install -r requirements.txt"
    exit 1
fi

# Test 4: Check environment variables
echo -e "\n4️⃣ Checking environment variables..."
if [ -f .env.local ]; then
    echo "✅ .env.local exists"
else
    echo "⚠️  .env.local not found. Copy from .env.example and configure"
fi

echo -e "\n✅ All checks passed! Ready to run SAMA 2.0"
echo -e "\nNext steps:"
echo "1. python setup.py          # Initialize database and keywords"
echo "2. uvicorn main:app --reload # Start SAMA"
echo "3. Visit http://localhost:8000/docs"
