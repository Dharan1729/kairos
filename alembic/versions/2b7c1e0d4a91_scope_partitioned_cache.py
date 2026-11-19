"""scope-partitioned bundle cache, person-bound credentials

- api_keys.person_id: bind a key to one directory person.
- tenant_configs.sso_user_claim: the JWT claim naming the person.
- cache_entries: replaced (the old retrieval/assembly cache rows keyed on a
  scope list and a per-scope version sum; they are discarded, a cache is
  disposable).
- audit_ledger: one cache_hit flag instead of retrieval/assembly flags; the
  per-scope version sum is no longer recorded.

Revision ID: 2b7c1e0d4a91
Revises: 8febff8b96f7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2b7c1e0d4a91"
down_revision: str | Sequence[str] | None = "8febff8b96f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("person_id", sa.String(), nullable=True))
    with op.batch_alter_table("tenant_configs") as batch:
        batch.add_column(sa.Column("sso_user_claim", sa.String(), nullable=True))

    op.drop_index(op.f("ix_cache_entries_scope_key"), table_name="cache_entries")
    op.drop_index(op.f("ix_cache_entries_tenant_id"), table_name="cache_entries")
    op.drop_table("cache_entries")
    op.create_table(
        "cache_entries",
        sa.Column("cache_key", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("partition_key", sa.String(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("bundle", sa.Text(), nullable=False),
        sa.Column("contributing", sa.Text(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index(op.f("ix_cache_entries_tenant_id"), "cache_entries", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_cache_entries_partition_key"), "cache_entries", ["partition_key"], unique=False)

    with op.batch_alter_table("audit_ledger") as batch:
        batch.alter_column(
            "assembly_cache_hit", new_column_name="cache_hit", existing_type=sa.Boolean(), existing_nullable=False
        )
        batch.drop_column("retrieval_cache_hit")
        batch.drop_column("knowledge_version_used")


def downgrade() -> None:
    with op.batch_alter_table("audit_ledger") as batch:
        batch.add_column(sa.Column("knowledge_version_used", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("retrieval_cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.alter_column(
            "cache_hit", new_column_name="assembly_cache_hit", existing_type=sa.Boolean(), existing_nullable=False
        )

    op.drop_index(op.f("ix_cache_entries_partition_key"), table_name="cache_entries")
    op.drop_index(op.f("ix_cache_entries_tenant_id"), table_name="cache_entries")
    op.drop_table("cache_entries")
    op.create_table(
        "cache_entries",
        sa.Column("cache_key", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("cache_type", sa.String(), nullable=False),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("budget_class", sa.Integer(), nullable=True),
        sa.Column("knowledge_version", sa.Integer(), nullable=False),
        sa.Column("bundle_ref", sa.Text(), nullable=False),
        sa.Column("ttl", sa.DateTime(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index(op.f("ix_cache_entries_scope_key"), "cache_entries", ["scope_key"], unique=False)
    op.create_index(op.f("ix_cache_entries_tenant_id"), "cache_entries", ["tenant_id"], unique=False)

    with op.batch_alter_table("tenant_configs") as batch:
        batch.drop_column("sso_user_claim")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("person_id")
