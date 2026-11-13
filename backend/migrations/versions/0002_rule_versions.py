"""rule versioning：ruleset_versions / mention_rule_attributions / ruleset_drafts / ruleset_publish_logs
+ burst_alerts 增加规则版本列与自然键。

Revision ID: 0002_rule_versions
Revises: 0001_initial
Create Date: 2026-11-13

- 新建 4 张表（规则版本、提及归属、整批草稿、发布日志）；
- 既有 burst_alerts 增加 ruleset_version / ruleset_version_id，自然键改为
  (group_id, window_start, ruleset_version)：历史预警按版本保留，不被新发布覆盖。
演示环境由 `scripts.bootstrap_db` 用同一份 Base.metadata 建表，并执行同一批幂等 DDL。
"""
from __future__ import annotations

from alembic import op

from src.models import Base
from src.config.settings import settings

revision = "0002_rule_versions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "ruleset_versions",
    "mention_rule_attributions",
    "ruleset_drafts",
    "ruleset_publish_logs",
)


def upgrade() -> None:
    bind = op.get_bind()
    schema = settings.db_schema
    bind.exec_driver_sql(
        f'ALTER TABLE "{schema}".burst_alerts ADD COLUMN IF NOT EXISTS ruleset_version INTEGER NOT NULL DEFAULT 0'
    )
    bind.exec_driver_sql(
        f'ALTER TABLE "{schema}".burst_alerts ADD COLUMN IF NOT EXISTS ruleset_version_id UUID NULL'
    )
    bind.exec_driver_sql(
        f'CREATE INDEX IF NOT EXISTS ix_burst_alerts_ruleset_version '
        f'ON "{schema}".burst_alerts (ruleset_version)'
    )
    bind.exec_driver_sql(
        f'CREATE INDEX IF NOT EXISTS ix_burst_alerts_ruleset_version_id '
        f'ON "{schema}".burst_alerts (ruleset_version_id)'
    )
    bind.exec_driver_sql(
        f'ALTER TABLE "{schema}".burst_alerts DROP CONSTRAINT IF EXISTS uq_burst_group_window'
    )
    bind.exec_driver_sql(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_burst_group_window_version'
            ) THEN
                ALTER TABLE "{schema}".burst_alerts
                ADD CONSTRAINT uq_burst_group_window_version
                UNIQUE (group_id, window_start, ruleset_version);
            END IF;
        END $$;
        """
    )

    tables = [Base.metadata.tables[f"{schema}.{name}"] for name in _NEW_TABLES]
    Base.metadata.create_all(bind=bind, tables=tables)


def downgrade() -> None:
    bind = op.get_bind()
    schema = settings.db_schema
    for name in reversed(_NEW_TABLES):
        Base.metadata.tables[f"{schema}.{name}"].drop(bind=bind)
    bind.exec_driver_sql(
        f'ALTER TABLE "{schema}".burst_alerts DROP CONSTRAINT IF EXISTS uq_burst_group_window_version'
    )
    bind.exec_driver_sql(
        f'ALTER TABLE "{schema}".burst_alerts '
        f'ADD CONSTRAINT uq_burst_group_window UNIQUE (group_id, window_start)'
    )
    bind.exec_driver_sql(f'ALTER TABLE "{schema}".burst_alerts DROP COLUMN IF EXISTS ruleset_version_id')
    bind.exec_driver_sql(f'ALTER TABLE "{schema}".burst_alerts DROP COLUMN IF EXISTS ruleset_version')
