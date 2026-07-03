from sqlalchemy import inspect, text

from app.database import engine


NEW_COLUMNS = [
    ("set_aside", "VARCHAR(256) NOT NULL DEFAULT ''"),
    ("extent_competed", "VARCHAR(256) NOT NULL DEFAULT ''"),
    ("solicitation_number", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("pursuit_score", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("notes", "TEXT NOT NULL DEFAULT ''"),
]


def run_migrations() -> None:
    inspector = inspect(engine)
    if "contracts" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("contracts")}
    with engine.begin() as conn:
        for name, col_type in NEW_COLUMNS:
            if name not in existing:
                conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {name} {col_type}"))
