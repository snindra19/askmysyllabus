from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Role every API transaction runs as, so RLS always applies (created in the first migration).
APP_ROLE = "askmysyllabus_app"

# API engine: each transaction starts as APP_ROLE with no user scope, so it sees nothing until scope_to_user.
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "begin")
def _switch_to_app_role(conn):
    conn.exec_driver_sql(f"SET LOCAL ROLE {APP_ROLE}")


# Owner engine: bypasses RLS. Only for workers and admin jobs (migrations use their own connection).
owner_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
OwnerSessionLocal = async_sessionmaker(owner_engine, expire_on_commit=False)


async def scope_to_user(session: AsyncSession, user_id: UUID) -> None:
    """Grant this user's RLS access until the current transaction ends."""
    await session.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})
