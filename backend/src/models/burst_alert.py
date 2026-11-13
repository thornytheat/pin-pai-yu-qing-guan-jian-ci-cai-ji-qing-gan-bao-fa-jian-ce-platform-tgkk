"""BurstAlert：关键词组×小时桶的爆发事件（实体 4/4）。

自然键 = (group_id, window_start, ruleset_version)：同一规则版本内重复检测只更新指标不新建；
规则发布后新检测产生新版本事件，历史预警保留原版本（不被覆盖、不被追溯修改）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.settings import settings
from src.models.base import Base, TimestampMixin, new_uuid


class BurstAlert(Base, TimestampMixin):
    __tablename__ = "burst_alerts"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "window_start", "ruleset_version", name="uq_burst_group_window_version"
        ),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.keyword_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    brand_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_mean: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    baseline_std: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    z_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, index=True)
    level: Mapped[str] = mapped_column(String(12), nullable=False, default="watch", index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    merged_windows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    baseline_series: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sample_titles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_by_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disposition_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detection_version: Mapped[str] = mapped_column(String(20), nullable=False, default="burst-1.0.0")
    ruleset_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    ruleset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.ruleset_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    group = relationship("KeywordGroup", lazy="joined")
