"""add stdout stderr to test_run_script_jobs

Revision ID: 0003_script_job_logs
Revises: 0002_test_run_script_jobs
Create Date: 2026-05-20 00:00:00
"""
"""
andan
"""


from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# type: ignore  MC8yOmFIVnBZMlhscm9ua3VMazZSbU5ZZGc9PTo0MzE1N2UzMA==


revision: str = "0003_script_job_logs"
down_revision: Union[str, None] = "0002_test_run_script_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("test_run_script_jobs") as batch:
        batch.add_column(sa.Column("stdout", sa.Text(), nullable=True))
        batch.add_column(sa.Column("stderr", sa.Text(), nullable=True))
# type: ignore  MS8yOmFIVnBZMlhscm9ua3VMazZSbU5ZZGc9PTo0MzE1N2UzMA==


def downgrade() -> None:
    with op.batch_alter_table("test_run_script_jobs") as batch:
        batch.drop_column("stderr")
        batch.drop_column("stdout")
