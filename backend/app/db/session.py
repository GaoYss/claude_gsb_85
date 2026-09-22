"""数据库引擎与会话管理。"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base


def _build_engine() -> Engine:
    url = settings.database_url
    options: dict = {"echo": settings.sql_echo, "pool_pre_ping": True}

    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        db_path = url.split("///")[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    return create_engine(url, **options)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """SQLite 默认不开外键约束，这里显式打开。"""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（轻量系统直接用 create_all，无需迁移框架）。"""
    from app import models  # noqa: F401  确保所有模型已注册

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """给已存在的老库补新列（create_all 不会 alter 已有表）。

    只处理「新增可空列」这一种安全场景；复杂变更仍需人工迁移。
    """
    from sqlalchemy import inspect, text

    additions = {
        "hazard_rectification": {
            "batch_id": "VARCHAR(64)",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

