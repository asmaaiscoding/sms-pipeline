"""002_add_exclusion_enums

Revision ID: 002
Create Date: 2026-03-04 00:00:00.000000

Add new enum values:
- projectstatus: 'excluding'
- jobtype: 'automated_exclusion', 'custom_pipeline'
"""

from alembic import op

# revision identifiers
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'excluding' to projectstatus enum
    op.execute("ALTER TYPE projectstatus ADD VALUE IF NOT EXISTS 'excluding' AFTER 'filtering'")

    # Add 'automated_exclusion' and 'custom_pipeline' to jobtype enum
    op.execute("ALTER TYPE jobtype ADD VALUE IF NOT EXISTS 'automated_exclusion' AFTER 'filter'")
    op.execute("ALTER TYPE jobtype ADD VALUE IF NOT EXISTS 'custom_pipeline' AFTER 'full_pipeline'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values easily.
    # This is a no-op for downgrade — the extra enum values are harmless.
    pass
