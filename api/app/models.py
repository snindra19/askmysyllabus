from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import declarative_base

from app.config import settings

Base = declarative_base()


def created_at():
    return Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class University(Base):
    __tablename__ = "universities"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True)
    email_domain = Column(String, nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, server_default="true")


class Major(Base):
    __tablename__ = "majors"
    id = Column(Integer, primary_key=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    degree_level = Column(String, nullable=False)


class MajorCourseRule(Base):
    __tablename__ = "major_course_rules"
    id = Column(Integer, primary_key=True)
    major_id = Column(Integer, ForeignKey("majors.id", ondelete="CASCADE"), nullable=False, index=True)
    catalog_year = Column(Integer, nullable=False)
    rule_type = Column(String, nullable=False)  # prefix | course
    value = Column(String, nullable=False)
    approved = Column(Boolean, nullable=False, server_default="false")


class Course(Base):
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False, index=True)
    code = Column(String, nullable=False)
    title = Column(String, nullable=False)
    professor = Column(String)
    term = Column(String, nullable=False)
    catalog_description = Column(Text)
    facts = Column(JSONB)
    summary = Column(JSONB)  # topics, outcomes, prerequisites
    summary_embedding = Column(Vector(settings.embedding_dim))


class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    source_url = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    file_hash = Column(String, nullable=False, unique=True)
    markdown = Column(Text)
    status = Column(String, nullable=False)
    error = Column(Text)
    version = Column(Integer, nullable=False, server_default="1")
    ingested_at = Column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(BigInteger, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    section_label = Column(String)
    parent_section_id = Column(Integer)  # groups chunks from one source section within a document
    is_boilerplate = Column(Boolean, nullable=False, server_default="false")
    content = Column(Text, nullable=False)  # includes the contextual header
    embedding = Column(Vector(settings.embedding_dim))
    tsv = Column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))

    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )


class AppUser(Base):
    __tablename__ = "app_users"
    id = Column(UUID(as_uuid=True), primary_key=True)  # Keycloak subject
    email = Column(String, nullable=False, unique=True)
    major_id = Column(Integer, ForeignKey("majors.id"))
    degree_level = Column(String)
    start_year = Column(Integer)
    role = Column(String, nullable=False, server_default="student")
    deleted_at = Column(DateTime(timezone=True))
    created_at = created_at()


class UserUniversityAccess(Base):
    __tablename__ = "user_university_access"
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    university_id = Column(Integer, ForeignKey("universities.id"), primary_key=True)
    access_type = Column(String, nullable=False)  # enrolled | prospective
    granted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id = Column(Integer, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_version = Column(String, nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip = Column(INET)
    user_agent = Column(String)
    withdrawn_at = Column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    university_id = Column(Integer, ForeignKey("universities.id"), nullable=False)
    term = Column(String)
    title = Column(String)
    created_at = created_at()
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"
    id = Column(BigInteger, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    rewritten_query = Column(Text)
    intent = Column(String)
    scope = Column(JSONB)
    route = Column(String)
    retrieved_chunk_ids = Column(ARRAY(BigInteger))
    rerank_scores = Column(ARRAY(Float))
    tier_results = Column(JSONB)
    abstained = Column(Boolean, nullable=False, server_default="false")
    refused = Column(Boolean, nullable=False, server_default="false")
    model = Column(String)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    latency_ms = Column(Integer)
    ttft_ms = Column(Integer)
    feedback = Column(SmallInteger)  # 1 up, -1 down
    created_at = created_at()


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    day = Column(Date, primary_key=True)
    questions = Column(Integer, nullable=False, server_default="0")


class UniversityRequest(Base):
    __tablename__ = "university_requests"
    id = Column(Integer, primary_key=True)
    university_name = Column(String, nullable=False)
    email = Column(String)
    user_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id", ondelete="SET NULL"))
    created_at = created_at()


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"
    id = Column(BigInteger, primary_key=True)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("app_users.id"), nullable=False)
    action = Column(String, nullable=False)
    target = Column(String)
    details = Column(JSONB)
    created_at = created_at()


class GoldenQuestion(Base):
    __tablename__ = "golden_questions"
    id = Column(Integer, primary_key=True)
    question = Column(Text, nullable=False)
    scope = Column(JSONB)
    intent = Column(String)
    expected_answer = Column(Text)
    expected_chunk_ids = Column(ARRAY(BigInteger))
    expected_courses = Column(ARRAY(Integer))
    expected_tier = Column(String)
    source = Column(String, nullable=False, server_default="seed")  # seed | production
