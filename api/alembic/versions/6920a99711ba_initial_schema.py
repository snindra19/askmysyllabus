"""initial schema

Revision ID: 6920a99711ba
Revises: 
Create Date: 2026-11-15 05:16:54.807488

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '6920a99711ba'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "askmysyllabus_app"
# Unset app.user_id matches nothing, so a forgotten scope fails closed.
CURRENT_USER_ID = "NULLIF(current_setting('app.user_id', true), '')::uuid"
OWN_ROWS = f"user_id = {CURRENT_USER_ID}"
# Each condition filters by the user explicitly, so it never depends on another table's policy.
OWN_UNIVERSITIES = f"university_id IN (SELECT university_id FROM user_university_access WHERE {OWN_ROWS})"
RLS_POLICIES = {
    "courses": OWN_UNIVERSITIES,
    "chunks": OWN_UNIVERSITIES,
    "documents": f"course_id IN (SELECT id FROM courses WHERE {OWN_UNIVERSITIES})",
    "app_users": f"id = {CURRENT_USER_ID}",
    "user_university_access": OWN_ROWS,
    "consent_records": OWN_ROWS,
    "conversations": OWN_ROWS,
    "messages": f"conversation_id IN (SELECT id FROM conversations WHERE {OWN_ROWS})",
    "daily_usage": OWN_ROWS,
}
# Privileges taken back from APP_ROLE after the blanket grant: the API needs no more than this.
REVOKES = {
    "alembic_version": "ALL",
    "admin_audit_log": "ALL",  # admin routes use the owner connection
    "golden_questions": "ALL",  # eval and admin only
    # Catalog content is written only by ingestion, the guidebook parser, and admin, all as the owner.
    "universities": "INSERT, UPDATE, DELETE",
    "majors": "INSERT, UPDATE, DELETE",
    "major_course_rules": "INSERT, UPDATE, DELETE",
    "courses": "INSERT, UPDATE, DELETE",
    "documents": "INSERT, UPDATE, DELETE",
    "chunks": "INSERT, UPDATE, DELETE",
    "user_university_access": "INSERT, UPDATE, DELETE",  # granted from the verified email domain by the owner
    "university_requests": "SELECT, UPDATE, DELETE",  # waitlist is submit-only for students
    # Account deletion sets app_users.deleted_at; the purge job deletes as the owner.
    "app_users": "DELETE",
    "conversations": "DELETE",
    "messages": "DELETE",
    "daily_usage": "DELETE",
    "consent_records": "UPDATE, DELETE",  # consent history is append-only
}


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('golden_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('intent', sa.String(), nullable=True),
    sa.Column('expected_answer', sa.Text(), nullable=True),
    sa.Column('expected_chunk_ids', postgresql.ARRAY(sa.BigInteger()), nullable=True),
    sa.Column('expected_courses', postgresql.ARRAY(sa.Integer()), nullable=True),
    sa.Column('expected_tier', sa.String(), nullable=True),
    sa.Column('source', sa.String(), server_default='seed', nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('universities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('slug', sa.String(), nullable=False),
    sa.Column('email_domain', sa.String(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email_domain'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('courses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('professor', sa.String(), nullable=True),
    sa.Column('term', sa.String(), nullable=False),
    sa.Column('catalog_description', sa.Text(), nullable=True),
    sa.Column('facts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('summary_embedding', Vector(1024), nullable=True),
    sa.ForeignKeyConstraint(['university_id'], ['universities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_courses_university_id'), 'courses', ['university_id'], unique=False)
    op.create_table('majors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('degree_level', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['university_id'], ['universities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_majors_university_id'), 'majors', ['university_id'], unique=False)
    op.create_table('app_users',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('major_id', sa.Integer(), nullable=True),
    sa.Column('degree_level', sa.String(), nullable=True),
    sa.Column('start_year', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(), server_default='student', nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['major_id'], ['majors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('source_url', sa.String(), nullable=False),
    sa.Column('storage_key', sa.String(), nullable=False),
    sa.Column('file_hash', sa.String(), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_hash')
    )
    op.create_index(op.f('ix_documents_course_id'), 'documents', ['course_id'], unique=False)
    op.create_table('major_course_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('major_id', sa.Integer(), nullable=False),
    sa.Column('catalog_year', sa.Integer(), nullable=False),
    sa.Column('rule_type', sa.String(), nullable=False),
    sa.Column('value', sa.String(), nullable=False),
    sa.Column('approved', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['major_id'], ['majors.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_major_course_rules_major_id'), 'major_course_rules', ['major_id'], unique=False)
    op.create_table('admin_audit_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('admin_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('target', sa.String(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['admin_id'], ['app_users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('chunks',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('section_label', sa.String(), nullable=True),
    sa.Column('parent_section_id', sa.Integer(), nullable=True),
    sa.Column('is_boilerplate', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', Vector(1024), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', content)", persisted=True), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['university_id'], ['universities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chunks_course_id'), 'chunks', ['course_id'], unique=False)
    op.create_index(op.f('ix_chunks_document_id'), 'chunks', ['document_id'], unique=False)
    op.create_index('ix_chunks_embedding_hnsw', 'chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ix_chunks_tsv', 'chunks', ['tsv'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_chunks_university_id'), 'chunks', ['university_id'], unique=False)
    op.create_table('consent_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('policy_version', sa.String(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['app_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_consent_records_user_id'), 'consent_records', ['user_id'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('term', sa.String(), nullable=True),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['university_id'], ['universities.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    op.create_table('daily_usage',
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('questions', sa.Integer(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'day')
    )
    op.create_table('university_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('university_name', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('user_university_access',
    sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('access_type', sa.String(), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['university_id'], ['universities.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['app_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'university_id')
    )
    op.create_table('messages',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('rewritten_query', sa.Text(), nullable=True),
    sa.Column('intent', sa.String(), nullable=True),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('route', sa.String(), nullable=True),
    sa.Column('retrieved_chunk_ids', postgresql.ARRAY(sa.BigInteger()), nullable=True),
    sa.Column('rerank_scores', postgresql.ARRAY(sa.Float()), nullable=True),
    sa.Column('tier_results', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('abstained', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('refused', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('ttft_ms', sa.Integer(), nullable=True),
    sa.Column('feedback', sa.SmallInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_conversation_id'), 'messages', ['conversation_id'], unique=False)

    # Every API transaction runs as APP_ROLE (SET LOCAL ROLE, see app/db.py) so RLS applies; the owner role
    # (migrations, ingest-worker, admin jobs) bypasses RLS.
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$
    """)
    op.execute(f"GRANT {APP_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    for table, privileges in REVOKES.items():
        op.execute(f"REVOKE {privileges} ON {table} FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT (id) ON university_requests TO {APP_ROLE}")  # INSERT ... RETURNING id
    for table, condition in RLS_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} TO {APP_ROLE} USING ({condition})")


def downgrade() -> None:
    for table in RLS_POLICIES:
        op.execute(f"DROP POLICY {table}_isolation ON {table}")
    op.drop_index(op.f('ix_messages_conversation_id'), table_name='messages')
    op.drop_table('messages')
    op.drop_table('user_university_access')
    op.drop_table('university_requests')
    op.drop_table('daily_usage')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_index(op.f('ix_consent_records_user_id'), table_name='consent_records')
    op.drop_table('consent_records')
    op.drop_index(op.f('ix_chunks_university_id'), table_name='chunks')
    op.drop_index('ix_chunks_tsv', table_name='chunks', postgresql_using='gin')
    op.drop_index('ix_chunks_embedding_hnsw', table_name='chunks', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index(op.f('ix_chunks_document_id'), table_name='chunks')
    op.drop_index(op.f('ix_chunks_course_id'), table_name='chunks')
    op.drop_table('chunks')
    op.drop_table('admin_audit_log')
    op.drop_index(op.f('ix_major_course_rules_major_id'), table_name='major_course_rules')
    op.drop_table('major_course_rules')
    op.drop_index(op.f('ix_documents_course_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_table('app_users')
    op.drop_index(op.f('ix_majors_university_id'), table_name='majors')
    op.drop_table('majors')
    op.drop_index(op.f('ix_courses_university_id'), table_name='courses')
    op.drop_table('courses')
    op.drop_table('universities')
    op.drop_table('golden_questions')
    op.execute(f"DROP OWNED BY {APP_ROLE}")
    op.execute(f"DROP ROLE {APP_ROLE}")
