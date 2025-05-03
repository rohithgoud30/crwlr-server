# Scripts

This directory contains utility scripts for database maintenance and other operations.

## Raw Text Removal Script

The `remove_raw_text.py` script is designed to remove raw text content from all documents in the database, setting the `raw_text` field to an empty string while preserving all other data.

### Why Remove Raw Text?

- **Reduces Database Size**: Raw document text can be very large, consuming significant storage space
- **Improves Performance**: Smaller database size means faster queries and less memory usage
- **Privacy and Security**: Minimizes potential exposure of sensitive data
- **Redundant Data**: The raw text is no longer needed since we have already extracted and stored summaries, word frequencies, and other metrics

### How to Use

```bash
# Navigate to project root
cd /path/to/crwlr-server

# Run the schema migration script first (only needed once)
python -m scripts.alter_raw_text_column

# Then run the raw text removal script
python -m scripts.remove_raw_text
```

### What the Scripts Do

#### alter_raw_text_column.py

1. Connects to the database using your existing configuration
2. Alters the `raw_text` column to make it nullable
3. Sets a default empty string value for new records

#### remove_raw_text.py

1. Connects to the database using your existing configuration
2. Finds all documents that have non-empty raw text content
3. Updates those documents to set `raw_text` to an empty string
4. Preserves the original `updated_at` timestamp to avoid affecting document sorting
5. Logs the number of documents processed

### Note

The application has also been updated to no longer store raw text for new documents that are added to the system. The `raw_text` field is still present in the database schema for backward compatibility, but will only contain an empty string for all new documents.
