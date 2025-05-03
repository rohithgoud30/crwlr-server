#!/bin/bash

# Script to run the database schema migration and raw text removal
# This script should be run from the project root directory

echo "Starting raw text cleanup process..."
echo "-------------------------------------"

# Step 1: Run schema migration to make raw_text nullable
echo "Step 1: Updating database schema..."
python -m scripts.alter_raw_text_column

# Check if migration was successful
if [ $? -ne 0 ]; then
    echo "Error: Schema migration failed. Aborting."
    exit 1
fi

# Step 2: Run raw text removal to clear existing data
echo "Step 2: Removing raw text from existing documents..."
python -m scripts.remove_raw_text

# Check if removal was successful
if [ $? -ne 0 ]; then
    echo "Error: Raw text removal failed."
    exit 1
fi

echo "-------------------------------------"
echo "Raw text cleanup completed successfully!"
echo "Database schema has been updated and raw text has been removed from all documents."
echo "Future documents will no longer store raw text content."
echo ""
echo "Check the application logs for detailed information." 