import asyncio
import warnings
import sys
import os
from sqlalchemy import delete, Table
from sqlalchemy.exc import SAWarning

# Add project root to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)

# Suppress specific SQLAlchemy warnings if necessary, e.g., about reflection
warnings.filterwarnings("ignore", category=SAWarning)

# Import your database engine and table definitions
# Adjust the import path based on your project structure
from app.core.database import async_engine, documents, submissions

async def clear_tables(tables: list[Table]):
    """Deletes all data from the specified tables within a transaction."""
    async with async_engine.begin() as conn:
        print("Starting transaction to clear tables...")
        for table in tables:
            try:
                table_name = table.name
                print(f"Attempting to clear table: {table_name}...")
                delete_stmt = delete(table)
                result = await conn.execute(delete_stmt)
                print(f"Cleared table: {table_name}. Rows affected: {result.rowcount}")
            except Exception as e:
                print(f"Error clearing table {table.name}: {e}")
                print("Rolling back transaction.")
                # The transaction will automatically roll back due to the exception
                raise # Re-raise the exception to stop the script
        print("Transaction committed successfully. All specified tables cleared.")

async def main():
    # List of tables to clear
    tables_to_clear = [documents, submissions]
    table_names = [t.name for t in tables_to_clear]
    
    print("\n*** WARNING: DATABASE CLEAR SCRIPT ***")
    print("This script will permanently delete ALL data from the following tables:")
    for name in table_names:
        print(f"  - {name}")
    print("This operation cannot be undone.")
    
    confirmation = input("\nAre you absolutely sure you want to proceed? (yes/no): ")
    
    # Accept 'yes' or 'y' (case-insensitive)
    if confirmation.lower() in ['yes', 'y']:
        print("Proceeding with database clearing...")
        try:
            await clear_tables(tables_to_clear)
            print("Database clearing process finished.")
        except Exception as e:
            print(f"Database clearing failed: {e}")
    else:
        print("Database clearing cancelled.")

if __name__ == "__main__":
    asyncio.run(main()) 