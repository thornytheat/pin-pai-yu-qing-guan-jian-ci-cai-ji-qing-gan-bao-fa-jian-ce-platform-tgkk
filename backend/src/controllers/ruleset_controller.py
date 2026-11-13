"""规则版本控制器：草稿 / 对照 / 发布 / 版本历史。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from src.middlewares.auth import brand_scope
from src.models.user import User
from src.schemas.ruleset import DraftRequest, PublishRequest
from src.services import ruleset_service


def overview(db: Session, user: User) -> dict:
    return ruleset_service.overview(db, brand_scope(user))


def save_draft(db: Session, payload: DraftRequest, user: User, trace_id: str = "") -> dict:
    return ruleset_service.save_draft_api(db, payload, user, trace_id)


def list_drafts(db: Session, user: User) -> dict:
    return ruleset_service.list_drafts_api(db, brand_scope(user))


def draft_detail(db: Session, draft_id: str, user: User) -> dict:
    return ruleset_service.draft_detail_api(db, draft_id, brand_scope(user))


def diff(db: Session, draft_id: str, user: User) -> dict:
    return ruleset_service.diff_api(db, draft_id, brand_scope(user))


def trial(db: Session, payload: DraftRequest, user: User) -> dict:
    return ruleset_service.trial_unsaved(db, payload, brand_scope(user))


def publish(db: Session, draft_id: str, payload: PublishRequest, user: User, trace_id: str = "") -> dict:
    return ruleset_service.publish_api(db, draft_id, payload.reason, user, trace_id)


def history(db: Session, user: User) -> dict:
    return ruleset_service.history_api(db, brand_scope(user))
