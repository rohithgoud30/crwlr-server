#!/bin/bash

# CRWLR Database Initialization Script
# This script initializes the database with the required tables and schema

set -e  # Exit immediately if a command fails

echo "======================================================"
echo "  CRWLR Database Initialization Script"
echo "======================================================"

# Check if python is available
if ! command -v python &> /dev/null; then
    echo "Error: python is not installed or not in PATH"
    exit 1
fi

# Current directory of the script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Warning: .env file not found. Database connection may fail."
    echo "Please create a .env file with the required database connection parameters:"
    echo "  DB_USER=postgres"
    echo "  DB_PASS=your_password"
    echo "  DB_NAME=postgres"
    echo "  DB_HOST=your-db-host"
    echo "  DB_PORT=5432"
    echo
    read -p "Continue anyway? (y/N): " CONTINUE
    if [[ ! $CONTINUE =~ ^[Yy]$ ]]; then
        echo "Initialization cancelled."
        exit 0
    fi
fi

echo "Initializing database tables..."

# Run Python script to create tables
python -c "from app.core.database import create_tables; create_tables()"

# Check if command succeeded
if [ $? -eq 0 ]; then
    echo "======================================================"
    echo "  Database initialization successful!"
    echo "======================================================"
    echo "  Tables created:"
    echo "  - users"
    echo "  - documents"
    echo "  - submissions"
    echo "======================================================"
else
    echo "======================================================"
    echo "  Error initializing database!"
    echo "  Please check your database connection settings in .env"
    echo "======================================================"
    exit 1
fi 