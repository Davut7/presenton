from datetime import datetime, timezone
import secrets
from typing import Optional
import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class AsyncPresentationGenerationTaskModel(SQLModel, table=True):

    __tablename__ = "async_presentation_generation_tasks"

    id: str = Field(
        default_factory=lambda: f"task-{secrets.token_hex(32)}", primary_key=True
    )
    status: str
    message: Optional[str] = None
    error: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: Optional[dict] = Field(sa_column=Column(JSON), default=None)
