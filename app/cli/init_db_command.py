import typer
import logging
from app.core.init_db import init_db

app = typer.Typer()
logger = logging.getLogger(__name__)

@app.command()
def create_tables():
    """Create all database tables"""
    try:
        init_db()
        typer.echo("Database tables created successfully!")
    except Exception as e:
        typer.echo(f"Error creating database tables: {e}", err=True)
        raise typer.Exit(code=1)

if __name__ == "__main__":
    app() 