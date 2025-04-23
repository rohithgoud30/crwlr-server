from sqlalchemy import inspect
from app.core.database import engine

inspector = inspect(engine)
table_names = inspector.get_table_names()

print("Database tables:")
for table in table_names:
    print(f"- {table}")
    
    # Get columns for each table
    columns = inspector.get_columns(table)
    print("  Columns:")
    for column in columns:
        print(f"    - {column['name']} ({column['type']})")
    
    print() 