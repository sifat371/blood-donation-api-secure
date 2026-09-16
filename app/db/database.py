from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, Session, create_engine

from app.core.config import settings


def _engine_kwargs(database_url: str) -> dict:
    """Return driver-specific engine options without leaking SQLite settings."""
    backend = make_url(database_url).get_backend_name()
    if backend == "sqlite":
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(
    settings.database_url,
    echo=settings.db_echo,
    **_engine_kwargs(settings.database_url),
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
