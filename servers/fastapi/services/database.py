from collections.abc import AsyncGenerator
import os
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.image_asset import ImageAsset
from models.sql.key_value import KeyValueSqlModel
from models.sql.ollama_pull_status import OllamaPullStatus
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from models.sql.template import TemplateModel
from models.sql.webhook_subscription import WebhookSubscription
from utils.db_utils import get_database_url_and_connect_args


database_url, connect_args = get_database_url_and_connect_args()

# Connection pool tuning. Postgres gets a real pool so 10+ parallel
# presentations don't block on connection acquisition; SQLite uses the
# default single-connection pool (it can't benefit from a larger pool).
_engine_kwargs: dict = {"connect_args": connect_args}
if database_url.startswith("postgresql"):
    _engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "30")),
        pool_pre_ping=True,
        pool_recycle=1800,
    )

sql_engine: AsyncEngine = create_async_engine(database_url, **_engine_kwargs)
async_session_maker = async_sessionmaker(sql_engine, expire_on_commit=False)


# WAL mode + busy_timeout on every SQLite connection. WAL lets readers and a
# single writer coexist, which is exactly the load profile we hit when 5-10
# presentations generate in parallel (each one polls + writes slides). Without
# WAL the default rollback-journal serialises everything and crashes with
# "database is locked".
if "sqlite" in database_url:
    @event.listens_for(sql_engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


# Container DB (Lives inside the container)
container_db_url = "sqlite+aiosqlite:////app/container.db"
container_db_engine: AsyncEngine = create_async_engine(
    container_db_url, connect_args={"check_same_thread": False}
)
container_db_async_session_maker = async_sessionmaker(
    container_db_engine, expire_on_commit=False
)


async def get_container_db_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with container_db_async_session_maker() as session:
        yield session


# Create Database and Tables
async def create_db_and_tables():
    async with sql_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SQLModel.metadata.create_all(
                sync_conn,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    KeyValueSqlModel.__table__,
                    ImageAsset.__table__,
                    PresentationLayoutCodeModel.__table__,
                    TemplateModel.__table__,
                    WebhookSubscription.__table__,
                    AsyncPresentationGenerationTaskModel.__table__,
                ],
            )
        )
        # Lightweight schema migration for existing DBs: ensure `presentations.theme` exists.
        if database_url.startswith("sqlite"):
            result = await conn.execute(text("PRAGMA table_info(presentations)"))
            column_names = {row[1] for row in result.fetchall()}
            if "theme" not in column_names:
                await conn.execute(text("ALTER TABLE presentations ADD COLUMN theme JSON"))

    async with container_db_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: SQLModel.metadata.create_all(
                sync_conn,
                tables=[OllamaPullStatus.__table__],
            )
        )
