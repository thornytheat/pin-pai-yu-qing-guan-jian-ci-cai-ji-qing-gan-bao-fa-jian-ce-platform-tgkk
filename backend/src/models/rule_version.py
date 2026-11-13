"""RulesetVersion：关键词规则的可预演、可追溯版本（草稿/线上并存）。

设计约束：
- 一次发布 = 每条启用中关键词组在这里的一行快照（keywords/synonyms/excludes/z/min_count/enabled）；
- 版本号单调递增；`is_current` 全局最多一行为 true（采集/提及归属/情感汇总/爆发预警的唯一口径）；
- 发布失败整体回滚：不留下半套版本、不翻转 is_current；
- 同一草稿内容重复发布 → 复用已发布版本，不新建版本。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.settings import settings
from src.models.base import Base, new_uuid, utcnow


class RulesetVersion(Base):
    __tablename__ = "ruleset_versions"
    __table_args__ = (
        UniqueConstraint("group_id", "version", name="uq_ruleset_group_version"),
        UniqueConstraint("group_id", "content_hash", name="uq_ruleset_group_hash"),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.keyword_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    synonyms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    excludes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    z_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    min_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eval_day: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    published_by_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    publish_reason: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    group = relationship("KeywordGroup", lazy="joined")


class MentionRuleAttribution(Base):
    """提及在「当前发布口径」下的归属结果（1 条提及一行）。

    由发布时的全量重算 / 采集时的即时判定写入；提及流、情感汇总、爆发预警都只读这张表，
    从而保证四个环节使用同一个发布版本。
    """
    __tablename__ = "mention_rule_attributions"
    __table_args__ = (
        UniqueConstraint("mention_id", name="uq_attribution_mention"),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    mention_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.mentions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.keyword_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ruleset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.ruleset_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    matched_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    matched_synonyms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    hit_excludes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(String(24), nullable=False, default="legacy")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RulesetDraft(Base):
    """整批草稿（一次发布单位）：线上版本 + 待发布的全部组快照。

    自然键 = content_hash（线上基线 + 全部组内容 + 阈值）。同一份草稿重复发布
    → 直接复用该草稿记录与它已发布出的版本，不新建版本、不重复统计。
    """
    __tablename__ = "ruleset_drafts"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_ruleset_draft_hash"),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    base_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    owner_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_trial: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    publish_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RulesetPublishLog(Base):
    """发布/失败审计：每次发布尝试一行（失败保留原版本，也在这里留痕）。"""
    __tablename__ = "ruleset_publish_logs"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.ruleset_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="failed", index=True)
    outcome_text: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
