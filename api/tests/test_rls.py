"""RLS: an API session sees only the scoped user's university content and own user rows, and nothing when unscoped.

Data is seeded and removed through the owner engine; every read and write goes through the API's SessionLocal.
"""

import datetime
import uuid

import pytest
from sqlalchemy import delete, func, insert, select, update

from app.db import OwnerSessionLocal, SessionLocal, scope_to_user
from app.models import (
    AdminAuditLog,
    AppUser,
    Chunk,
    ConsentRecord,
    Conversation,
    Course,
    DailyUsage,
    Document,
    GoldenQuestion,
    MajorCourseRule,
    Message,
    University,
    UniversityRequest,
    UserUniversityAccess,
)

# Table -> (column to look up, key of the seeded value in seed_university's result)
CHECKS = {
    "courses": (Course.id, "course"),
    "documents": (Document.id, "doc"),
    "chunks": (Chunk.id, "chunk"),
    "app_users": (AppUser.id, "user"),
    "user_university_access": (UserUniversityAccess.user_id, "user"),
    "consent_records": (ConsentRecord.id, "consent"),
    "conversations": (Conversation.id, "conversation"),
    "messages": (Message.id, "message"),
    "daily_usage": (DailyUsage.user_id, "user"),
}
NOTHING = {table: set() for table in CHECKS}


async def seed_university(session, slug):
    uni = University(name=slug, slug=slug, email_domain=f"{slug}.edu")
    user = AppUser(id=uuid.uuid4(), email=f"student@{slug}.edu")
    session.add_all([uni, user])
    await session.flush()
    course = Course(university_id=uni.id, code="CSE 101", title="Intro", term="Fall 2026")
    conversation = Conversation(user_id=user.id, university_id=uni.id)
    consent = ConsentRecord(user_id=user.id, policy_version="1")
    session.add_all([
        course,
        conversation,
        consent,
        UserUniversityAccess(user_id=user.id, university_id=uni.id, access_type="enrolled"),
        DailyUsage(user_id=user.id, day=datetime.date.today(), questions=1),
    ])
    await session.flush()
    doc = Document(course_id=course.id, source_url="https://x", storage_key=slug, file_hash=uuid.uuid4().hex, status="done")
    message = Message(conversation_id=conversation.id, role="user", content="when is the midterm?")
    session.add_all([doc, message])
    await session.flush()
    chunk = Chunk(document_id=doc.id, university_id=uni.id, course_id=course.id, content="grading policy")
    session.add(chunk)
    await session.flush()
    return {
        "uni": uni.id, "user": user.id, "course": course.id, "doc": doc.id, "chunk": chunk.id,
        "conversation": conversation.id, "message": message.id, "consent": consent.id,
    }


@pytest.fixture(scope="module")
async def unis():
    suffix = uuid.uuid4().hex[:8]
    async with OwnerSessionLocal() as s:
        a = await seed_university(s, f"rls-a-{suffix}")
        b = await seed_university(s, f"rls-b-{suffix}")
        await s.commit()
    yield a, b
    async with OwnerSessionLocal() as s:
        await s.execute(delete(Course).where(Course.university_id.in_([a["uni"], b["uni"]])))  # cascades docs, chunks
        await s.execute(delete(AppUser).where(AppUser.id.in_([a["user"], b["user"]])))  # cascades all user rows
        await s.execute(delete(University).where(University.id.in_([a["uni"], b["uni"]])))
        await s.commit()


async def visible(session, unis):
    """Which of the seeded rows this session can see, per table."""
    result = {}
    for table, (column, key) in CHECKS.items():
        stmt = select(column).where(column.in_([u[key] for u in unis]))
        result[table] = set((await session.execute(stmt)).scalars())
    return result


async def test_api_session_without_scope_sees_nothing(unis):
    async with SessionLocal() as s:
        assert await visible(s, unis) == NOTHING


async def test_user_sees_only_own_rows(unis):
    a, b = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        assert await visible(s, unis) == {table: {a[key]} for table, (_, key) in CHECKS.items()}
        fts = select(Chunk.id).where(Chunk.tsv.match("grading"), Chunk.id.in_([a["chunk"], b["chunk"]]))
        assert set((await s.execute(fts)).scalars()) == {a["chunk"]}


async def test_scope_ends_with_transaction(unis):
    a, _ = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        await s.commit()
        assert await visible(s, unis) == NOTHING


async def test_user_without_access_sees_nothing(unis):
    async with SessionLocal() as s:
        await scope_to_user(s, uuid.uuid4())
        assert await visible(s, unis) == NOTHING


@pytest.mark.parametrize(
    "make_insert",
    [
        lambda b: insert(Course).values(university_id=b["uni"], code="X 1", title="X", term="Fall 2026"),
        lambda b: insert(Document).values(course_id=b["course"], source_url="x", storage_key="x", file_hash=uuid.uuid4().hex, status="done"),
        lambda b: insert(Chunk).values(document_id=b["doc"], university_id=b["uni"], course_id=b["course"], content="x"),
        lambda b: insert(Conversation).values(user_id=b["user"], university_id=b["uni"]),
        lambda b: insert(Message).values(conversation_id=b["conversation"], role="user", content="x"),
        lambda b: insert(ConsentRecord).values(user_id=b["user"], policy_version="1"),
        lambda b: insert(DailyUsage).values(user_id=b["user"], day=datetime.date(2000, 1, 1)),
    ],
    ids=["course", "document", "chunk", "conversation", "message", "consent", "daily_usage"],
)
async def test_cannot_insert_rows_for_other_user_or_university(unis, make_insert):
    a, b = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        # Catalog tables are read-only for the app role (permission error); user tables are blocked by RLS.
        with pytest.raises(Exception, match="row-level security|permission denied"):
            await s.execute(make_insert(b))


@pytest.mark.parametrize(
    "make_stmt",
    [
        lambda b: update(Conversation).where(Conversation.id == b["conversation"]).values(title="x"),
        lambda b: update(Message).where(Message.id == b["message"]).values(content="x"),
        lambda b: update(AppUser).where(AppUser.id == b["user"]).values(major_id=None),
    ],
    ids=["update-conversation", "update-message", "update-user"],
)
async def test_other_users_rows_are_not_changed(unis, make_stmt):
    a, b = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        assert (await s.execute(make_stmt(b))).rowcount == 0


@pytest.mark.parametrize(
    "make_stmt",
    [
        lambda a, b: insert(UserUniversityAccess).values(user_id=a["user"], university_id=b["uni"], access_type="enrolled"),
        lambda a, b: update(UserUniversityAccess).values(access_type="prospective"),
        lambda a, b: delete(UserUniversityAccess),
        lambda a, b: select(AdminAuditLog),
        lambda a, b: select(GoldenQuestion),
        lambda a, b: insert(MajorCourseRule).values(major_id=1, catalog_year=2026, rule_type="prefix", value="CSE"),
        lambda a, b: select(UniversityRequest.email),
        # Even the user's own rows: deletion is soft (deleted_at), the purge job deletes as the owner.
        lambda a, b: delete(AppUser).where(AppUser.id == a["user"]),
        lambda a, b: delete(Conversation).where(Conversation.id == a["conversation"]),
        lambda a, b: delete(Message).where(Message.id == a["message"]),
        lambda a, b: delete(DailyUsage).where(DailyUsage.user_id == a["user"]),
        lambda a, b: delete(ConsentRecord).where(ConsentRecord.id == a["consent"]),
        lambda a, b: update(ConsentRecord).where(ConsentRecord.id == a["consent"]).values(policy_version="2"),
    ],
    ids=[
        "insert-access", "update-access", "delete-access", "read-audit-log", "read-golden", "write-rules",
        "read-request-emails", "delete-user", "delete-conversation", "delete-message", "delete-daily-usage",
        "delete-consent", "update-consent",
    ],
)
async def test_app_role_lacks_privilege(unis, make_stmt):
    a, b = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        with pytest.raises(Exception, match="permission denied"):
            await s.execute(make_stmt(a, b))


async def test_user_can_soft_delete_own_account(unis):
    a, _ = unis
    async with SessionLocal() as s:
        await scope_to_user(s, a["user"])
        stmt = update(AppUser).where(AppUser.id == a["user"]).values(deleted_at=func.now())
        assert (await s.execute(stmt)).rowcount == 1
        await s.rollback()


async def test_app_role_can_submit_university_request():
    async with SessionLocal() as s:
        await s.execute(insert(UniversityRequest).values(university_name="Example State"))
        await s.rollback()
