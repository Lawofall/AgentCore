"""drop simulation_run / sim_tick / sim_agent / sim_event

Revision ID: c9e2a7f4b1d6
Revises: e2f9a6c3d8b1
Create Date: 2026-08-28 00:22:00.000000

AI Town simulation is removed from AgentCore server. Keep historical
``f9a1b2c3d4e5`` (create) in the chain; this revision drops the four tables.
``downgrade`` rebuilds the final shape but does not restore deleted rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e2a7f4b1d6"
down_revision: str | None = "e2f9a6c3d8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_sim_event_run_tick", table_name="sim_event")
    op.drop_index(op.f("ix_sim_event_run_id"), table_name="sim_event")
    op.drop_table("sim_event")
    op.drop_index("ix_sim_agent_run_agent", table_name="sim_agent")
    op.drop_index(op.f("ix_sim_agent_run_id"), table_name="sim_agent")
    op.drop_table("sim_agent")
    op.drop_index("ix_sim_tick_run_tick", table_name="sim_tick")
    op.drop_index(op.f("ix_sim_tick_run_id"), table_name="sim_tick")
    op.drop_table("sim_tick")
    op.drop_index(op.f("ix_simulation_run_user_id"), table_name="simulation_run")
    op.drop_table("simulation_run")


def downgrade() -> None:
    op.create_table(
        "simulation_run",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "scenario",
            sa.String(length=64),
            server_default=sa.text("'town'"),
            nullable=False,
        ),
        sa.Column("seed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("current_tick", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_simulation_run_user_id"), "simulation_run", ["user_id"], unique=False)

    op.create_table(
        "sim_tick",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("tick_number", sa.Integer(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sim_tick_run_id"), "sim_tick", ["run_id"], unique=False)
    op.create_index("ix_sim_tick_run_tick", "sim_tick", ["run_id", "tick_number"], unique=True)

    op.create_table(
        "sim_agent",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=200), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "persona",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("location", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "position",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("mood", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("activity", sa.String(length=500), server_default=sa.text("''"), nullable=False),
        sa.Column("goal", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "state_extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sim_agent_run_id"), "sim_agent", ["run_id"], unique=False)
    op.create_index("ix_sim_agent_run_agent", "sim_agent", ["run_id", "agent_id"], unique=True)

    op.create_table(
        "sim_event",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("tick_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sim_event_run_id"), "sim_event", ["run_id"], unique=False)
    op.create_index("ix_sim_event_run_tick", "sim_event", ["run_id", "tick_number"], unique=False)
