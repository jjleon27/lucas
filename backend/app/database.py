"""
SQLAlchemy engine / session factory. `get_db()` is the FastAPI dependency
every router uses to get a request-scoped session.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables on startup. For real migrations, use Alembic."""
    # Import models so they register with Base.metadata before create_all.
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def _migrate_schema() -> None:
    """
    Lightweight column-level migrations for existing DBs.
    create_all() only adds new *tables*; for new columns on old tables we
    need explicit ALTER TABLE statements. Each ALTER is wrapped in its own
    try/except so a duplicate-column error is silently swallowed.
    """
    from sqlalchemy import text
    stmts = [
        # people.is_me — added in the v2 split redesign
        "ALTER TABLE people ADD COLUMN is_me BOOLEAN NOT NULL DEFAULT FALSE",
        # accounts.card_image_url — user photo or preset key for card visual
        "ALTER TABLE accounts ADD COLUMN card_image_url VARCHAR(512) DEFAULT ''",
        # transactions.status — "confirmed" (default) | "pending_review" (email/auto-import)
        "ALTER TABLE transactions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'confirmed'",
        # users.email_token — unique token for personal forwarding email address
        "ALTER TABLE users ADD COLUMN email_token VARCHAR(64)",
        # users.auth_provider — which provider created the account
        "ALTER TABLE users ADD COLUMN auth_provider VARCHAR(32) NOT NULL DEFAULT 'password'",
        # bill_item_shares.units — number of individual units assigned (e.g. 2 of 6 schops)
        "ALTER TABLE bill_item_shares ADD COLUMN units REAL",
    ]
    with engine.connect() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                conn.rollback()
