"""建表引导：幂等 create_all（容器 entrypoint 第一步）。

演示环境用 SQLAlchemy metadata 建表，避免首启卡在迁移版本对齐；
正式环境可执行 `alembic upgrade head`（见 backend/migrations/）。
"""
from __future__ import annotations

from sqlalchemy import text

from src.config.database import engine
from src.config.settings import settings
from src.models import Base


def _migrate_burst_natural_key(conn) -> None:
    """0002：burst_alerts 自然键加上 ruleset_version（历史预警按版本归档）。

    create_all 不会改既有表约束，这里用幂等 DDL 对齐：先删旧约束再建新约束。
    """
    conn.execute(
        text(
            f'ALTER TABLE "{settings.db_schema}".burst_alerts '
            f'DROP CONSTRAINT IF EXISTS uq_burst_group_window'
        )
    )
    conn.execute(
        text(
            f'ALTER TABLE "{settings.db_schema}".burst_alerts '
            f'ADD CONSTRAINT uq_burst_group_window_version '
            f'UNIQUE (group_id, window_start, ruleset_version)'
        )
    )


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        # 老库（0001 建出的 burst_alerts）先补版本列，再换自然键
        conn.execute(
            text(
                f'ALTER TABLE "{settings.db_schema}".burst_alerts '
                f'ADD COLUMN IF NOT EXISTS ruleset_version INTEGER NOT NULL DEFAULT 0'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{settings.db_schema}".burst_alerts '
                f'ADD COLUMN IF NOT EXISTS ruleset_version_id UUID NULL'
            )
        )
        _migrate_burst_natural_key(conn)
    print(f"[bootstrap] schema={settings.db_schema} tables={len(Base.metadata.tables)}", flush=True)


if __name__ == "__main__":
    main()
