from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.controllers import ruleset_controller
from src.middlewares.rate_limit import rate_limit
from src.middlewares.rbac import require_permission
from src.models.user import User
from src.schemas.ruleset import DraftRequest, PublishRequest

router = APIRouter(tags=["ruleset"])


@router.get("/rulesets/overview")
def overview(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:read")),
) -> dict:
    return ruleset_controller.overview(db, user)


@router.get("/rulesets/drafts")
def list_drafts(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:read")),
) -> dict:
    return ruleset_controller.list_drafts(db, user)


@router.post("/rulesets/drafts")
def save_draft(
    payload: DraftRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:write")),
) -> dict:
    return ruleset_controller.save_draft(db, payload, user, getattr(request.state, "trace_id", ""))


@router.get("/rulesets/drafts/{draft_id}")
def draft_detail(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:read")),
) -> dict:
    return ruleset_controller.draft_detail(db, draft_id, user)


@router.post("/rulesets/drafts/{draft_id}/diff")
def diff(
    draft_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:trial")),
    _: None = rate_limit("default"),
) -> dict:
    return ruleset_controller.diff(db, draft_id, user)


@router.post("/rulesets/trial")
def trial(
    payload: DraftRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:trial")),
    _: None = rate_limit("default"),
) -> dict:
    return ruleset_controller.trial(db, payload, user)


@router.post("/rulesets/drafts/{draft_id}/publish")
def publish(
    draft_id: str,
    payload: PublishRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:publish")),
    _: None = rate_limit("detect"),
) -> dict:
    return ruleset_controller.publish(db, draft_id, payload, user, getattr(request.state, "trace_id", ""))


@router.get("/rulesets/history")
def history(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("groups:read")),
) -> dict:
    return ruleset_controller.history(db, user)
