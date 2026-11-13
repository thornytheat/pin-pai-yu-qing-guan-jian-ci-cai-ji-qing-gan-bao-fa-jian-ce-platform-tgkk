"""FastAPI 应用装配：中间件、异常处理、路由挂载、启动 seed。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config.settings import settings
from src.middlewares.audit_logger import AuditContextMiddleware
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.timing import TimingMiddleware
from src.routes import api_router

logger = logging.getLogger("newssweep.app")


def _run_seed_once() -> None:
    """SEED_DEMO=true 时启动阶段执行一次幂等 seed；失败只记 WARNING，绝不阻塞启动。"""
    if not settings.seed_demo:
        return
    try:
        from scripts.seed_demo import seed_all

        result = seed_all()
        logger.warning(
            "[SEED_DEMO] seed finished created=%s skipped=%s", result.get("created", 0), result.get("skipped", 0)
        )
    except Exception as exc:  # noqa: BLE001 - 启动阶段不允许阻塞
        logger.warning("[SEED_DEMO] seed failed (ignored): %s", exc)


def _backfill_ruleset_once() -> None:
    """老库升级兜底：给没有规则版本的组建立 v1、给没有归属行的提及补归属。

    幂等且失败不阻塞启动；不改变任何已发布版本或历史预警。
    """
    try:
        from sqlalchemy import select

        from src.config.database import session_scope
        from src.models.keyword_group import KeywordGroup
        from src.models.rule_version import RulesetVersion
        from src.services import ruleset_service

        with session_scope() as db:
            has_versions = db.execute(select(RulesetVersion.id).limit(1)).first()
            if has_versions is None and db.execute(select(KeywordGroup.id).limit(1)).first() is None:
                return
            created = ruleset_service.seed_versions(db, actor_email="system")
            stats = ruleset_service.backfill_attributions(db)
        logger.warning("[RULESET] baseline versions=%s backfill=%s", created, stats)
    except Exception as exc:  # noqa: BLE001 - 启动阶段不允许阻塞
        logger.warning("[RULESET] backfill skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_seed_once()
    _backfill_ruleset_once()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="newssweep · 品牌舆情关键词采集与情感/爆发监测台",
        description="4 源采集 · 中文情感词典打分 · 关键词×小时桶爆发检测",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditContextMiddleware)
    app.add_middleware(TimingMiddleware)
    register_exception_handlers(app)

    # 同一份路由挂两次：/api/* 给直连；根路径给 Nginx 剥掉 /api 后的反代
    app.include_router(api_router, prefix="/api")
    app.include_router(api_router, include_in_schema=False)
    return app
