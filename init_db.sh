#!/bin/bash

# Initialize database tables for CRWLR
# This script creates all the database tables in the Cloud SQL instance

set -e

echo "Initializing database tables..."

# Import the necessary Python module and run the initialization
python -c "from app.core.init_db import init_db; init_db()"

echo "Database initialization completed successfully!" 