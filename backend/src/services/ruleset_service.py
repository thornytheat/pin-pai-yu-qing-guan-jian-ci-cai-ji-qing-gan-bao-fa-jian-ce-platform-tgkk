"""规则版本服务：草稿 / 线上版本 / 同批候选对照 / 一次性发布。

核心口径（全系统唯一）：
- 一个关键词组某时刻生效的规则 = `ruleset_versions` 里该组 `is_current=true` 的那一行；
- 提及归属 = `mention_rule_attributions`（每条提及只在当前口径下有一行）；
- 采集、提及归属、情感汇总、爆发预警全部只读这两张表；
- 历史预警/历史统计不回溯：发布只从发布时刻起对新提及生效，旧提及的归属保持原样。

可预演：`diff` 用**同一批候选**（采集窗口 92 条 + 已入库提及）分别套用线上规则与草稿规则，
逐条给出「新增 / 漏掉 / 被排除 / 归属变化」以及情感标签与爆发窗口差异。

可追溯：每次发布一个递增版本号；发布失败整体回滚，线上版本不变；同一草稿内容重复发布
→ 复用已发布版本（幂等，不新建版本、不重复统计）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants import error_codes as codes
from src.constants.enums import BRAND_LABELS, SOURCE_LABELS
from src.constants.log_templates import (
    ACTION_RULESET_DRAFT,
    ACTION_RULESET_PUBLISH,
    ACTION_RULESET_PUBLISH_FAILED,
)
from src.models.burst_alert import BurstAlert
from src.models.user import User
from src.services import audit_service
from src.models.keyword_group import KeywordGroup
from src.models.mention import Mention
from src.models.rule_version import (
    MentionRuleAttribution,
    RulesetDraft,
    RulesetPublishLog,
    RulesetVersion,
)
from src.services.analysis.burst_detector import MentionPoint, detect
from src.services.analysis.sentiment_analyzer import analyze, analyze_keyword_recall
from src.services.fixtures_loader import inbox_corpus
from src.utils.errors import AppError
from src.utils.timeutil import TZ_SHANGHAI, utcnow

DEMO_CANDIDATE_LIMIT = 60
# 采集窗口候选的 external_id 前缀：这些条目即使已被采集入库，也仍作为新一轮候选参与对照。
INBOX_PREFIX = "inbox:"


def _aware(value: datetime) -> datetime:
    """排序用：库内是 aware UTC，SQLite 测试驱动可能回 naive，统一补成 UTC 再比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ_SHANGHAI)
    return value


def to_uuid(value: object) -> uuid.UUID | None:
    """把 API 传入的字符串主键收敛成 uuid 对象（SQLite 测试驱动与 Postgres 一致）。"""
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None

# ---------------------------------------------------------------- 版本快照 ----


def rule_of(version: RulesetVersion) -> dict:
    return {
        "keywords": list(version.keywords or []),
        "synonyms": list(version.synonyms or []),
        "excludes": list(version.excludes or []),
        "z_threshold": version.z_threshold,
        "min_count": version.min_count,
        "enabled": version.enabled,
    }


def snapshot_hash(rule: dict) -> str:
    payload = {
        "keywords": list(rule.get("keywords") or []),
        "synonyms": list(rule.get("synonyms") or []),
        "excludes": list(rule.get("excludes") or []),
        "z_threshold": round(float(rule.get("z_threshold", 0)), 4),
        "min_count": int(rule.get("min_count", 0)),
        "enabled": bool(rule.get("enabled", True)),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current_versions(db: Session) -> dict[str, RulesetVersion]:
    rows = db.execute(select(RulesetVersion).where(RulesetVersion.is_current.is_(True))).scalars().all()
    return {str(row.group_id): row for row in rows}


def current_rule(db: Session, group_id: str) -> RulesetVersion | None:
    key = to_uuid(group_id)
    if key is None:
        return None
    return db.execute(
        select(RulesetVersion).where(
            RulesetVersion.group_id == key, RulesetVersion.is_current.is_(True)
        )
    ).scalar_one_or_none()


def version_for_group(db: Session, group_id: str, version: int) -> RulesetVersion:
    row = db.execute(
        select(RulesetVersion).where(
            RulesetVersion.group_id == to_uuid(group_id), RulesetVersion.version == version
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(codes.NOT_FOUND, "规则版本不存在", f"group_id={group_id} version={version}")
    return row


def version_history(db: Session, group: KeywordGroup) -> list[dict]:
    rows = db.execute(
        select(RulesetVersion)
        .where(RulesetVersion.group_id == group.id)
        .order_by(RulesetVersion.version.desc())
    ).scalars().all()
    return [serialize_version(row) for row in rows]


def serialize_version(row: RulesetVersion) -> dict:
    return {
        "id": str(row.id),
        "group_id": str(row.group_id),
        "version": row.version,
        "parent_version": row.parent_version,
        "is_current": row.is_current,
        "keywords": row.keywords,
        "synonyms": row.synonyms,
        "excludes": row.excludes,
        "z_threshold": row.z_threshold,
        "min_count": row.min_count,
        "enabled": row.enabled,
        "content_hash": row.content_hash,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "eval_day": row.eval_day,
        "published_by_email": row.published_by_email,
        "publish_reason": row.publish_reason,
    }


# -------------------------------------------------------------- 规则版本基线 ----


def ensure_baseline(db: Session, group: KeywordGroup, *, actor_email: str = "", reason: str = "bootstrap") -> RulesetVersion:
    """为还没有任何版本的组建 v1 = 当前 KeywordGroup 字段（seed 与启动兜底用，不写归属）。"""
    existing = current_rule(db, str(group.id))
    if existing is not None:
        return existing
    rule = {
        "keywords": list(group.keywords or []),
        "synonyms": list(group.synonyms or []),
        "excludes": list(group.excludes or []),
        "z_threshold": group.z_threshold,
        "min_count": group.min_count,
        "enabled": group.enabled,
    }
    return _insert_version(db, group, rule, version=1, parent=None, actor_email=actor_email, reason=reason, is_current=True)


def _insert_version(
    db: Session,
    group: KeywordGroup,
    rule: dict,
    *,
    version: int,
    parent: int | None,
    actor_email: str,
    reason: str,
    is_current: bool,
) -> RulesetVersion:
    row = RulesetVersion(
        group_id=group.id,
        version=version,
        parent_version=parent,
        is_current=is_current,
        keywords=list(rule["keywords"]),
        synonyms=list(rule["synonyms"]),
        excludes=list(rule["excludes"]),
        z_threshold=round(float(rule["z_threshold"]), 4),
        min_count=int(rule["min_count"]),
        enabled=bool(rule["enabled"]),
        content_hash=snapshot_hash(rule),
        started_at=utcnow(),
        eval_day=_anchor_day(db),
        published_by_email=actor_email,
        publish_reason=reason,
    )
    db.add(row)
    db.flush()
    return row


def seed_versions(db: Session, *, actor_email: str = "") -> int:
    """seed 用：给每个组建立 v1（已存在的组跳过）。返回新建行数。"""
    created = 0
    for group in db.execute(select(KeywordGroup)).scalars().all():
        if current_rule(db, str(group.id)) is not None:
            continue
        ensure_baseline(db, group, actor_email=actor_email, reason="seed")
        created += 1
    return created


def baseline_digest(db: Session) -> str:
    rows = db.execute(
        select(RulesetVersion).where(RulesetVersion.is_current.is_(True)).order_by(RulesetVersion.group_id)
    ).scalars().all()
    payload = [[str(row.group_id), row.version, row.content_hash] for row in rows]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _anchor_day(db: Session) -> str:
    anchor = db.execute(select(func.max(Mention.published_at))).scalar_one_or_none()
    if anchor is None:
        return ""
    if anchor.tzinfo is None:
        return anchor.date().isoformat()
    return anchor.astimezone(TZ_SHANGHAI).date().isoformat()


# ---------------------------------------------------------------- 草稿 ----


def _draft_item(group: KeywordGroup, rule: dict) -> dict:
    return {
        "group_id": str(group.id),
        "code": group.code,
        "name": group.name,
        "brand_key": group.brand_key,
        "rule": {
            "keywords": list(rule.get("keywords") or []),
            "synonyms": list(rule.get("synonyms") or []),
            "excludes": list(rule.get("excludes") or []),
            "z_threshold": float(rule.get("z_threshold", 3.0)),
            "min_count": int(rule.get("min_count", 5)),
            "enabled": bool(rule.get("enabled", True)),
        },
    }


def build_draft_items(db: Session, overrides: Iterable[dict], group_id: str | None = None) -> list[dict]:
    """把「线上版本 + 逐组覆盖」展开成完整草稿快照（供发布与对照复用）。"""
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    current = current_versions(db)
    items: list[dict] = []
    for gid, group in groups.items():
        live = current.get(gid)
        base_rule = rule_of(live) if live else {
            "keywords": group.keywords,
            "synonyms": group.synonyms,
            "excludes": group.excludes,
            "z_threshold": group.z_threshold,
            "min_count": group.min_count,
            "enabled": group.enabled,
        }
        items.append(_draft_item(group, base_rule))
    for override in overrides:
        key = str(override.get("group_id") or override.get("code") or "")
        target = None
        for item in items:
            if item["group_id"] == key or item["code"] == key:
                target = item
                break
        if target is None:
            continue
        target["rule"].update(
            {k: override[k] for k in ("keywords", "synonyms", "excludes") if k in override and override[k] is not None}
        )
        for k in ("z_threshold", "min_count", "enabled"):
            if k in override and override[k] is not None:
                target["rule"][k] = override[k]
    return items


def draft_hash(base_digest: str, items: list[dict]) -> str:
    payload = {
        "base": base_digest,
        "items": [
            {"group_id": item["group_id"], "rule": item["rule"], "hash": snapshot_hash(item["rule"])}
            for item in sorted(items, key=lambda it: it["group_id"])
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save_draft(db: Session, overrides: list[dict], *, label: str, group_id: str | None, user_email: str) -> dict:
    base = baseline_digest(db)
    items = build_draft_items(db, overrides, group_id)
    digest = draft_hash(base, items)
    draft = db.execute(select(RulesetDraft).where(RulesetDraft.content_hash == digest)).scalar_one_or_none()
    if draft is None:
        draft = RulesetDraft(
            label=label or "未命名草稿",
            content_hash=digest,
            base_digest=base,
            status="draft",
            items=items,
            owner_email=user_email,
        )
        db.add(draft)
    else:
        draft.items = items
        draft.label = label or draft.label
        if draft.status != "published":
            draft.status = "draft"
    db.flush()
    return draft


def list_drafts(db: Session) -> list[dict]:
    rows = db.execute(select(RulesetDraft).order_by(RulesetDraft.updated_at.desc(), RulesetDraft.created_at.desc())).scalars().all()
    return [serialize_draft(row) for row in rows]


def get_draft(db: Session, draft_id: str) -> RulesetDraft:
    draft = db.get(RulesetDraft, to_uuid(draft_id))
    if draft is None:
        raise AppError(codes.RULESET_DRAFT_NOT_FOUND, "规则草稿不存在", f"draft_id={draft_id}")
    return draft



def serialize_draft(row: RulesetDraft) -> dict:
    return {
        "id": str(row.id),
        "label": row.label,
        "content_hash": row.content_hash,
        "base_digest": row.base_digest,
        "status": row.status,
        "items": row.items,
        "owner_email": row.owner_email,
        "last_trial": row.last_trial,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "published_by_email": row.published_by_email,
        "publish_result": row.publish_result,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ------------------------------------------------------------ 候选与归属 ----


def _candidates(db: Session, brand_scope: str | None, limit: int | None) -> dict:
    """同一批候选：采集窗口原始候选（未入库）+ 库内已保留提及。

    两者都按 external_id 去重；批次上限对两段分别生效，保证「同一批候选」在
    多次对照之间完全一致（顺序稳定、条数稳定）。
    """
    rows = db.execute(
        select(
            Mention.id,
            Mention.external_id,
            Mention.group_id,
            Mention.brand_key,
            Mention.source_code,
            Mention.title,
            Mention.body,
            Mention.published_at,
            Mention.engagement,
        ).order_by(Mention.published_at.desc())
    ).all()
    stored: dict[str, dict] = {}
    for row in rows:
        if brand_scope and row[3] != brand_scope:
            continue
        external_id = row[1]
        if external_id in stored:
            continue
        stored[external_id] = {
            "origin": "mention",
            "mention_id": str(row[0]),
            "external_id": external_id,
            "current_group_id": str(row[2]),
            "brand_key": row[3],
            "source_code": row[4],
            "title": row[5],
            "body": row[6],
            "published_at": row[7],
            "engagement": row[8],
        }
    stored_items = sorted(stored.values(), key=lambda entry: (_aware(entry["published_at"]), entry["external_id"]), reverse=True)
    groups_by_code = {
        group.code: str(group.id) for group in db.execute(select(KeywordGroup)).scalars().all()
    }

    # 库内的补采候选（inbox:000..）仍按「采集窗口候选」演示：采集窗口是新一批来源候选。
    inbox: list[dict] = []
    stored_groups = {external_id: entry["current_group_id"] for external_id, entry in stored.items()}
    for item in inbox_corpus():
        if item["external_id"] in stored:
            # 已入库条目：仍作为「库内提及」参与对照（归属与情感差异），不重复计入窗口候选。
            continue
        if brand_scope and item["brand_key"] != brand_scope:
            continue
        inbox.append(
            {
                "origin": "inbox",
                "mention_id": str(stored[item["external_id"]]["mention_id"]) if item["external_id"] in stored else None,
                "external_id": item["external_id"],
                "current_group_id": stored_groups.get(item["external_id"]),
                "retrieval_group_id": str(groups_by_code[item["group_code"]]) if item["group_code"] in groups_by_code else None,
                "brand_key": item["brand_key"],
                "source_code": item["source_code"],
                "title": item["title"],
                "body": item["body"],
                "published_at": item["published_at"],
                "engagement": item["engagement"],
            }
        )
    inbox.sort(key=lambda entry: (_aware(entry["published_at"]), entry["external_id"]), reverse=True)
    if limit is None:
        return {"inbox": inbox, "stored": stored_items}
    return {"inbox": inbox[:limit], "stored": stored_items[: max(limit // 2, 1)]}


def _retrieval_id(entry: dict) -> str | None:
    return entry.get("current_group_id") or entry.get("retrieval_group_id")


def classify(text: str, rule: dict) -> dict:
    return analyze_keyword_recall(text, rule.get("keywords") or [], rule.get("synonyms") or [], rule.get("excludes") or [])


def attribute(
    entry: dict,
    brand_rules: dict[str, list[tuple[str, dict]]],
    retrieval_group_id: str | None = None,
    allow_retrieval_fallback: bool = True,
) -> dict | None:
    """在某个规则口径下判定一条候选的归属。

    判定顺序（与采集口径一致）：
    1. 品牌内任一启用规则命中主关键词/同义词 → 归属命中数最多、优先级最高的组（`matched`；
       若与检索组不同则视为换组）；
    2. 检索组本身启用且未被自身排除词命中 → 保留在检索组（`retrieved`，来源检索即为召回依据）；
    3. 检索组被自身排除词命中 → `excluded`（被排除，不计入提及流/汇总/预警）；
    4. 其余 → None（未命中）。

    检索组 = 采集中该候选被哪个来源检索捞出（fixture 的 `group_code`，或已入库提及当时的组）。
    """
    text = f"{entry['title']} {entry['body']}"
    best: tuple[int, int, str, dict] | None = None
    retrieval_excludes: list[str] = []
    retrieval_enabled = False
    for code, group_id, rule, priority in brand_rules.get(entry["brand_key"], []):
        if not rule.get("enabled", True):
            continue
        result = classify(text, rule)
        is_retrieval = retrieval_group_id is not None and group_id == retrieval_group_id
        if is_retrieval:
            retrieval_enabled = True
            retrieval_excludes = list(result["hit_excludes"])
        if not result["kept"]:
            continue
        score = len(result["hit_keywords"]) + len(result["hit_synonyms"])
        candidate = (score, priority, code, {"group_id": group_id, **result})
        if best is None or (score, priority) > (best[0], best[1]):
            best = candidate
    if best is not None:
        return {
            "group_id": best[3]["group_id"],
            "code": best[2],
            "reason": "matched",
            "hit_keywords": best[3]["hit_keywords"],
            "hit_synonyms": best[3]["hit_synonyms"],
            "hit_excludes": best[3]["hit_excludes"],
        }
    if retrieval_excludes:
        return {"group_id": None, "reason": "excluded", "hit_excludes": sorted(set(retrieval_excludes))}
    if allow_retrieval_fallback and retrieval_group_id is not None and retrieval_enabled:
        return {
            "group_id": retrieval_group_id,
            "reason": "retrieved",
            "hit_keywords": [],
            "hit_synonyms": [],
            "hit_excludes": [],
        }
    return None


def _brand_index(groups: dict[str, KeywordGroup], rules_by_group: dict[str, dict]) -> dict[str, list]:
    index: dict[str, list] = {}
    for gid, group in groups.items():
        rule = rules_by_group.get(gid)
        if rule is None:
            continue
        index.setdefault(group.brand_key, []).append((group.code, gid, rule, group.priority))
    for brand in index:
        index[brand].sort(key=lambda entry: (-entry[3], entry[0]))
    return index


def live_rules(db: Session) -> tuple[dict[str, KeywordGroup], dict[str, dict]]:
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    current = current_versions(db)
    rules: dict[str, dict] = {}
    for gid, group in groups.items():
        live = current.get(gid)
        if live is not None:
            rules[gid] = rule_of(live)
        else:
            rules[gid] = {
                "keywords": group.keywords,
                "synonyms": group.synonyms,
                "excludes": group.excludes,
                "z_threshold": group.z_threshold,
                "min_count": group.min_count,
                "enabled": group.enabled,
            }
    return groups, rules


def draft_rules(db: Session, draft_items: list[dict], groups: dict[str, KeywordGroup] | None = None) -> tuple[dict[str, KeywordGroup], dict[str, dict]]:
    if groups is None:
        groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    rules = {item["group_id"]: dict(item["rule"]) for item in draft_items if item["group_id"] in groups}
    return groups, rules


# ---------------------------------------------------------------- 对照 ----


def _sentiment_map(texts: list[str], label_cache: dict[str, dict]) -> None:
    for text in texts:
        if text in label_cache:
            continue
        result = analyze(text)
        label_cache[text] = {
            "label": result.label,
            "score": result.score,
            "confidence": result.confidence,
            "hits": result.hits,
            "negative_count": result.negative_count,
        }


def _bursts(points: list[MentionPoint], groups: dict[str, KeywordGroup], rules: dict[str, dict]) -> dict:
    thresholds = {
        gid: (rule["z_threshold"], rule["min_count"])
        for gid, rule in rules.items()
        if gid in groups and rule.get("enabled", True)
    }
    result = detect(points, group_thresholds=thresholds)
    events = []
    for event in result["events"]:
        group = groups.get(event.group_id)
        events.append(
            {
                "group_id": event.group_id,
                "code": group.code if group else event.group_id,
                "name": group.name if group else event.group_id,
                "window_start": event.window_start.isoformat(),
                "count": event.count,
                "z_score": round(event.z_score, 4),
                "level": event.level,
                "baseline_mean": round(event.baseline_mean, 4),
            }
        )
    return {"eval_day": result["eval_day"].isoformat() if result["eval_day"] else None, "events": events, "unknown": result["unknown_windows"]}




def _empty_compare() -> dict:
    return {"added": 0, "dropped": 0, "excluded": 0, "moved": 0, "unchanged": 0}


def diff(db: Session, draft: RulesetDraft, *, brand_scope: str | None = None, limit: int = DEMO_CANDIDATE_LIMIT) -> dict:
    return diff_against(
        db,
        draft.items,
        draft.base_digest,
        brand_scope=brand_scope,
        limit=limit,
        draft_id=str(draft.id),
        content_hash=draft.content_hash,
    )


def diff_against(
    db: Session,
    draft_items: list[dict],
    base_digest_value: str,
    *,
    brand_scope: str | None = None,
    limit: int = DEMO_CANDIDATE_LIMIT,
    draft_id: str | None = None,
    content_hash: str = "",
) -> dict:
    """同一批候选 × 新旧规则逐条对照。

    输出：
    - trial.rows：采集窗口候选（未入库）在旧/新规则下的命中与排除明细
    - mentions.rows：库内提及的归属与情感标签差异
    - sentiment / burst：汇总差异（同一批候选上新旧口径的分布与爆发事件）
    """
    groups, old_rules = live_rules(db)
    _, new_rules = draft_rules(db, draft_items, groups)
    candidates = _candidates(db, brand_scope, limit)
    trial_entries, stored_entries = candidates["inbox"], candidates["stored"]
    candidates = trial_entries + stored_entries

    old_index = _brand_index(groups, old_rules)
    new_index = _brand_index(groups, new_rules)

    label_cache: dict[str, dict] = {}
    rows: list[dict] = []
    compare = _empty_compare()
    sentiment_delta = {"old": {"pos": 0, "neu": 0, "neg": 0}, "new": {"pos": 0, "neu": 0, "neg": 0}}

    attribution = _attribution_index(db, stored_entries)
    for entry in candidates:
        text = f"{entry['title']} {entry['body']}"
        # 线上侧：库内提及以归属表为准（历史事实），窗口候选按当前线上规则重放（与采集同一口径）。
        old_hit = attribution.get(entry["external_id"]) if entry["origin"] == "mention" else None
        if old_hit is None:
            old_hit = attribute(entry, old_index, _retrieval_id(entry), allow_retrieval_fallback=True)
        # 草稿侧：历史归属若本来就来自规则命中，则新规则可以把它漏掉/排除；
        # 若历史归属来自检索组或 seed（从未依赖文本规则），则仍以检索组兜底，不做追溯否定。
        fallback = entry["origin"] == "inbox" or (old_hit or {}).get("reason") in ("retrieved", "seed", None)
        new_hit = attribute(entry, new_index, _retrieval_id(entry), allow_retrieval_fallback=fallback)
        old_label = old_hit is not None and old_hit["reason"] in ("matched", "retrieved", "seed")
        new_label = new_hit is not None and new_hit["reason"] in ("matched", "retrieved", "seed")
        old_excluded = bool(old_hit and old_hit["reason"] == "excluded")
        new_excluded = bool(new_hit and new_hit["reason"] == "excluded")

        change = "unchanged"
        if not old_label and new_label:
            change = "added"
        elif old_label and not new_label:
            change = "dropped" if not new_excluded else "excluded"
        elif old_label and new_label:
            if old_hit.get("group_id") and new_hit.get("group_id") and old_hit["group_id"] != new_hit["group_id"]:
                change = "moved"
            elif old_hit.get("reason") in ("retrieved", "seed") and new_hit.get("reason") == "matched":
                # 从「检索召回」升级为「规则显式命中」：归属没变，但命中证据变了
                change = "unchanged" if old_hit.get("group_id") == new_hit.get("group_id") else "moved"
        compare[change] = compare.get(change, 0) + 1

        _sentiment_map([text], label_cache)
        sentiment = label_cache[text]
        if old_label:
            sentiment_delta["old"][sentiment["label"]] += 1
        if new_label:
            sentiment_delta["new"][sentiment["label"]] += 1

        rows.append(
            {
                "external_id": entry["external_id"],
                "origin": entry["origin"],
                "mention_id": entry["mention_id"],
                "title": entry["title"],
                "source_code": entry["source_code"],
                "source_label": SOURCE_LABELS.get(entry["source_code"], entry["source_code"]),
                "brand_key": entry["brand_key"],
                "published_at": entry["published_at"].isoformat(),
                "change": change,
                "sentiment": {
                    "label": sentiment["label"],
                    "score": sentiment["score"],
                    "hits": [hit["word"] for hit in sentiment["hits"][:4]],
                },
                "old": _hit_payload(old_hit, groups),
                "new": _hit_payload(new_hit, groups),
            }
        )

    # 情感差异：只看归属发生变化（新增/漏掉/被排除/换组）的候选，逐条列出情感证据
    sentiment_rows = [row for row in rows if row["change"] != "unchanged"]

    # 爆发差异用整批库内提及（同一批候选），而不是截断后的预览样本：
    # 预览样本条数太少时不构成任何基线，会把「无差异」误报成结论。
    burst_entries = _candidates(db, brand_scope, None)["stored"]
    old_points = _points_for(burst_entries, old_index)
    new_points = _points_for(burst_entries, new_index)
    old_burst = _bursts(old_points, groups, old_rules)
    new_burst = _bursts(new_points, groups, new_rules)
    old_keys = {(event["group_id"], event["window_start"]): event for event in old_burst["events"]}
    new_keys = {(event["group_id"], event["window_start"]): event for event in new_burst["events"]}
    burst_rows = []
    for key in sorted(set(old_keys) | set(new_keys), key=lambda item: (item[0], item[1])):
        before, after = old_keys.get(key), new_keys.get(key)
        if before and after:
            if (before["level"], before["z_score"], before["count"]) == (after["level"], after["z_score"], after["count"]):
                change = "unchanged"
            else:
                change = "changed"
            burst_rows.append({"change": change, "old": before, "new": after})
        elif before:
            burst_rows.append({"change": "dropped", "old": before, "new": None})
        else:
            burst_rows.append({"change": "added", "old": None, "new": after})

    return {
        "draft_id": draft_id,
        "content_hash": content_hash,
        "base_digest": base_digest_value,
        "batch": {
            "total": len(candidates),
            "trial_or_inbox": len(trial_entries),
            "stored": len(stored_entries),
            "note": "同一批候选 = 采集窗口原始候选 + 库内提及（external_id 去重）",
        },
        "compare": compare,
        "rows": rows,
        "trial": {"rows": [row for row in rows if row["origin"] == "inbox"], "compare": _subset_compare(rows, "inbox")},
        "mentions": {
            "rows": [row for row in rows if row["origin"] == "mention"],
            "compare": _subset_compare(rows, "mention"),
        },
        "sentiment": {
            "old": sentiment_delta["old"],
            "new": sentiment_delta["new"],
            "changed_rows": sentiment_rows[:20],
            "note": "情感口径不变：同一批候选上新旧规则召回的条目分布差异；逐条列出证据词供核对",
        },
        "burst": {"old": old_burst, "new": new_burst, "rows": burst_rows},
        "rules": {
            "old": _rule_rows(groups, old_rules),
            "new": _rule_rows(groups, new_rules),
        },
    }


def _subset_compare(rows: list[dict], origin: str) -> dict:
    subset = _empty_compare()
    for row in rows:
        if row["origin"] == origin:
            subset[row["change"]] = subset.get(row["change"], 0) + 1
    return subset


def _hit_payload(hit: dict | None, groups: dict[str, KeywordGroup]) -> dict:
    if hit is None:
        return {"reason": "not_matched", "group_id": None, "code": None, "name": None, "hits": [], "excludes": []}
    group = groups.get(hit.get("group_id")) if hit.get("group_id") else None
    return {
        "reason": hit["reason"],
        "group_id": hit.get("group_id"),
        "code": group.code if group else None,
        "name": group.name if group else None,
        "hits": list(hit.get("hit_keywords") or []) + list(hit.get("hit_synonyms") or []),
        "excludes": list(hit.get("hit_excludes") or []),
    }


def _rule_rows(groups: dict[str, KeywordGroup], rules: dict[str, dict]) -> list[dict]:
    rows = []
    for gid, rule in sorted(rules.items(), key=lambda item: groups[item[0]].code if item[0] in groups else item[0]):
        group = groups.get(gid)
        if group is None:
            continue
        rows.append(
            {
                "group_id": gid,
                "code": group.code,
                "name": group.name,
                "keywords": rule["keywords"],
                "synonyms": rule["synonyms"],
                "excludes": rule["excludes"],
                "z_threshold": rule["z_threshold"],
                "min_count": rule["min_count"],
                "enabled": rule["enabled"],
                "hash": snapshot_hash(rule),
            }
        )
    return rows


def _points_for(entries: list[dict], brand_index: dict[str, list]) -> list[MentionPoint]:
    """把候选按某个规则口径映射成爆发检测点（未被召回的候选不进入计数）。"""
    points: list[MentionPoint] = []
    for entry in entries:
        hit = attribute(entry, brand_index, _retrieval_id(entry))
        if not hit or hit["reason"] not in ("matched", "retrieved") or not hit.get("group_id"):
            continue
        points.append(
            MentionPoint(
                group_id=hit["group_id"],
                brand_key=entry["brand_key"],
                published_at=entry["published_at"],
                title=entry["title"],
                engagement=entry["engagement"],
            )
        )
    return points


def counters(db: Session, brand_scope: str | None = None, *, limit: int = DEMO_CANDIDATE_LIMIT) -> dict:
    """线上口径下的候选计数（看板「同一批候选」的两栏对照用）：仅线上规则。"""
    groups, rules = live_rules(db)
    if brand_scope:
        groups = {gid: group for gid, group in groups.items() if group.brand_key == brand_scope}
        rules = {gid: rule for gid, rule in rules.items() if gid in groups}
    index = _brand_index(groups, rules)
    batch = _candidates(db, brand_scope, limit)
    entries = batch["inbox"] + batch["stored"]
    kept = 0
    excluded = 0
    for entry in entries:
        hit = attribute(entry, index, _retrieval_id(entry))
        if hit is None:
            continue
        if hit["reason"] in ("matched", "retrieved"):
            kept += 1
        elif hit["reason"] == "excluded":
            excluded += 1
    return {"candidates": len(entries), "inbox": len(batch["inbox"]), "stored": len(batch["stored"]), "kept": kept, "excluded": excluded}


# ---------------------------------------------------------------- 发布 ----


def _apply_rule_to_group(group: KeywordGroup, rule: dict) -> None:
    group.keywords = list(rule["keywords"])
    group.synonyms = list(rule["synonyms"])
    group.excludes = list(rule["excludes"])
    group.enabled = bool(rule["enabled"])
    group.z_threshold = float(rule["z_threshold"])
    group.min_count = int(rule["min_count"])


def publish(db: Session, draft: RulesetDraft, *, user_email: str, reason: str = "", trace_id: str = "") -> dict:
    """一次性发布：要么全部组的新版本同时生效，要么一行不改（事务由调用方 commit/rollback）。

    幂等：同一 content_hash 重复发布 → 返回已有版本，不新建版本、不重算统计。
    """
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    mismatch = [item["code"] for item in draft.items if item["group_id"] not in groups]
    if mismatch:
        raise AppError(codes.RULESET_STALE, "草稿引用的关键词组已不存在", f"codes={mismatch}")

    if draft.status == "published" and draft.publish_result:
        # 幂等：返回首次发布的结果；不新建版本、不重算归属、不写新统计。
        replay = {
            **draft.publish_result,
            "idempotent": True,
            "new_versions": {},
            "unchanged_versions": dict(draft.publish_result.get("versions") or {}),
        }
        return replay

    items = {str(to_uuid(item["group_id"])): item for item in draft.items}
    if not items:
        raise AppError(codes.VALIDATION_ERROR, "草稿为空，无法发布", "items=[]")

    # 草稿基于的线上基线必须仍是当前基线：否则说明期间线上被改过，草稿已过期。
    if draft.base_digest and draft.base_digest != baseline_digest(db):
        raise AppError(codes.RULESET_STALE, "草稿基于的线上版本已被修改，请重新对照后再发布", "base digest changed")

    current = current_versions(db)
    eval_day = _anchor_day(db)
    published_versions: dict[str, int] = {}
    new_rows: list[RulesetVersion] = []
    reused: dict[str, int] = {}
    for gid, item in sorted(items.items()):
        rule = item["rule"]
        digest = snapshot_hash(rule)
        live = current.get(gid)
        if live is not None and live.content_hash == digest:
            reused[item["code"]] = live.version  # 该组规则未变：沿用当前版本号，不新建
            continue
        group_uuid = to_uuid(gid)
        history = db.execute(
            select(RulesetVersion)
            .where(RulesetVersion.group_id == group_uuid)
            .order_by(RulesetVersion.version.desc())
        ).scalars().all()
        parent = history[0].version if history else None
        max_version = max((row.version for row in history), default=0)
        versions = max_version + 1
        row = RulesetVersion(
            group_id=group_uuid,
            version=versions,
            parent_version=parent,
            is_current=True,
            keywords=rule["keywords"],
            synonyms=rule["synonyms"],
            excludes=rule["excludes"],
            z_threshold=rule["z_threshold"],
            min_count=rule["min_count"],
            enabled=rule["enabled"],
            content_hash=digest,
            started_at=utcnow(),
            eval_day=eval_day,
            published_by_email=user_email,
            publish_reason=reason,
        )
        db.add(row)
        new_rows.append(row)
        published_versions[item["code"]] = versions
        _apply_rule_to_group(groups[str(group_uuid)], rule)
    db.flush()

    # 旧版本：同一组只留下唯一一个 is_current（关闭被替换的旧版本，历史行保留）
    published = {row.group_id: row.id for row in new_rows}
    replaced = {row.group_id: row.version for row in new_rows}
    for row in db.execute(select(RulesetVersion).where(RulesetVersion.is_current.is_(True))).scalars().all():
        if row.group_id in replaced and row.version != replaced[row.group_id]:
            row.is_current = False
            row.ended_at = utcnow()

    # 历史提及与历史统计不被追溯修改：发布只对「之后采集进来的提及」生效。
    # 归属表里每条提及仍指向评估它的那个版本（ruleset_version_id），历史预警同理。
    stats = {"applies_to": "new_mentions", "historical_mentions_preserved": True}
    result = {
        "draft_id": str(draft.id),
        "content_hash": draft.content_hash,
        "eval_day": eval_day,
        "versions": {**reused, **published_versions},
        "new_versions": published_versions,
        "unchanged_versions": reused,
        "reoptimized": stats,
        "changed_groups": sorted(published_versions),
        "published_at": utcnow().isoformat(),
        "published_by_email": user_email,
        "idempotent": False,
    }
    draft.status = "published"
    draft.published_at = utcnow()
    draft.published_by_email = user_email
    draft.publish_result = result
    return result


def record_publish_log(
    db: Session,
    *,
    draft: RulesetDraft | None,
    content_hash: str,
    outcome: str,
    outcome_text: str,
    versions: dict | None,
    actor_email: str,
    trace_id: str = "",
) -> None:
    db.add(
        RulesetPublishLog(
            draft_id=draft.id if draft else None,
            content_hash=content_hash,
            outcome=outcome,
            outcome_text=outcome_text[:400],
            versions=versions or {},
            actor_email=actor_email,
            trace_id=trace_id,
        )
    )


# ------------------------------------------------------- 归属（唯一口径） ----


def write_attribution(
    db: Session,
    *,
    mention: Mention,
    hit: dict | None,
    version_by_group: dict[str, RulesetVersion],
) -> MentionRuleAttribution:
    """把一条提及在当前发布口径下的归属结果写进 attribution 表（同一提及只有一行）。"""
    row = db.execute(
        select(MentionRuleAttribution).where(MentionRuleAttribution.mention_id == mention.id)
    ).scalar_one_or_none()
    if row is None:
        row = MentionRuleAttribution(mention_id=mention.id, group_id=mention.group_id)
        db.add(row)
        db.flush()

    if hit is not None and hit.get("reason") in ("matched", "retrieved") and hit.get("group_id"):
        group_id = to_uuid(hit["group_id"])
    else:
        # 未命中/被排除：仍保留原组，但 kept=false（不进入提及流、汇总与爆发计数）
        group_id = row.group_id or mention.group_id
    version = version_by_group.get(str(group_id))
    row.group_id = group_id
    row.ruleset_version_id = version.id if version else None
    row.kept = bool(hit is not None and hit.get("reason") in ("matched", "retrieved") and hit.get("group_id"))
    row.matched_keywords = list((hit or {}).get("hit_keywords") or [])
    row.matched_synonyms = list((hit or {}).get("hit_synonyms") or [])
    row.hit_excludes = list((hit or {}).get("hit_excludes") or [])
    row.reason = (hit or {}).get("reason") or "not_matched"
    if not row.kept and not row.hit_excludes:
        row.reason = "not_matched"
    if row.kept and group_id is not None:
        mention.group_id = group_id
    row.evaluated_at = utcnow()
    return row


def version_map(db: Session) -> dict[str, RulesetVersion]:
    return {str(row.group_id): row for row in db.execute(select(RulesetVersion).where(RulesetVersion.is_current.is_(True))).scalars().all()}


def evaluate_and_store(db: Session, mentions: list[Mention]) -> dict:
    """对给定提及按当前发布口径重算归属（采集与发布共用这一个入口）。"""
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    _, rules = live_rules(db)
    index = _brand_index(groups, rules)
    versions = version_map(db)
    stats = {"kept": 0, "excluded": 0, "not_matched": 0, "moved": 0}
    for mention in mentions:
        entry = {
            "title": mention.title or "",
            "body": mention.body or "",
            "brand_key": mention.brand_key,
        }
        hit = attribute(entry, index, str(mention.group_id))
        previous_group = str(mention.group_id)
        row = write_attribution(db, mention=mention, hit=hit, version_by_group=versions)
        if row.kept:
            mention.group_id = row.group_id
            if str(row.group_id) != previous_group:
                stats["moved"] += 1
            stats["kept"] += 1
        elif row.reason == "excluded":
            stats["excluded"] += 1
        else:
            stats["not_matched"] += 1
    return stats


def backfill_attributions(db: Session) -> dict:
    """启动兜底：给还没有归属行的提及补一条（按当时线上规则）。"""
    missing = db.execute(
        select(Mention).outerjoin(
            MentionRuleAttribution, MentionRuleAttribution.mention_id == Mention.id
        ).where(MentionRuleAttribution.id.is_(None))
    ).scalars().all()
    if not missing:
        return {"backfilled": 0, "kept": 0}
    stats = evaluate_and_store(db, list(missing))
    return {"backfilled": len(missing), **stats}


# ------------------------------------------------------------ API 编排 ----


def _overrides(payload_changes: list) -> list[dict]:
    rows: list[dict] = []
    for change in payload_changes:
        data = change.model_dump(exclude_none=True) if hasattr(change, "model_dump") else dict(change)
        rows.append(data)
    return rows


def overview(db: Session, brand_scope: str | None) -> dict:
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    current = current_versions(db)
    items = []
    for gid, group in groups.items():
        if brand_scope and group.brand_key != brand_scope:
            continue
        version = current.get(gid)
        items.append(
            {
                "group_id": gid,
                "code": group.code,
                "name": group.name,
                "brand_key": group.brand_key,
                "brand_label": BRAND_LABELS.get(group.brand_key, group.brand_key),
                "live_version": version.version if version else 0,
                "live_started_at": version.started_at.isoformat() if version else None,
                "live_published_by": version.published_by_email if version else "",
                "rule": rule_of(version) if version else {
                    "keywords": group.keywords,
                    "synonyms": group.synonyms,
                    "excludes": group.excludes,
                    "z_threshold": group.z_threshold,
                    "min_count": group.min_count,
                    "enabled": group.enabled,
                },
            }
        )
    items.sort(key=lambda item: item["code"])
    drafts = db.execute(
        select(RulesetDraft).order_by(RulesetDraft.updated_at.desc()).limit(20)
    ).scalars().all()
    aligned = _alignment(db)
    return {
        "groups": items,
        "current_digest": baseline_digest(db),
        "drafts": [serialize_draft(row) for row in drafts],
        "alignment": aligned,
        "batch": counters(db, brand_scope),
        "eval_day": _anchor_day(db),
    }


def _alignment(db: Session) -> dict:
    """四个环节是否同一口径：归属行、当前版本、预警版本三者对照（运维方可一眼验证）。"""
    total = int(db.execute(select(func.count(Mention.id))).scalar_one())
    attributed = int(db.execute(select(func.count(MentionRuleAttribution.id))).scalar_one())
    kept = int(
        db.execute(
            select(func.count(MentionRuleAttribution.id)).where(MentionRuleAttribution.kept.is_(True))
        ).scalar_one()
    )
    current = current_versions(db)
    current_ids = [row.id for row in current.values()]
    # 历史归属指向的版本可能已被新版本取代（这是正常的），只有「指向不存在的版本」才是失配。
    version_ids = {row[0] for row in db.execute(select(RulesetVersion.id)).all()}
    stale_attr = int(
        db.execute(
            select(func.count(MentionRuleAttribution.id)).where(
                MentionRuleAttribution.ruleset_version_id.is_not(None),
                MentionRuleAttribution.ruleset_version_id.notin_(list(version_ids)),
            )
        ).scalar_one()
    ) if version_ids else attributed
    alert_versions = sorted(
        row[0] for row in db.execute(select(BurstAlert.ruleset_version).distinct()).all()
    )
    return {
        "mentions": total,
        "attributed": attributed,
        "kept": kept,
        "stale_attributions": stale_attr,
        "current_version_ids": sorted(str(value) for value in current_ids),
        "alert_versions": alert_versions,
        "aligned": attributed == total and stale_attr == 0,
    }


def save_draft_api(db: Session, payload, user: User, trace_id: str = "") -> dict:
    draft = save_draft(
        db,
        _overrides(payload.changes),
        label=payload.label,
        group_id=None,
        user_email=user.email,
    )
    audit_service.record(
        db,
        action=ACTION_RULESET_DRAFT,
        actor=user,
        entity_type="ruleset_draft",
        entity_id=draft.id,
        detail={"hash": draft.content_hash[:12], "groups": len(payload.changes), "label": draft.label},
        trace_id=trace_id,
    )
    db.commit()
    return serialize_draft(draft)


def list_drafts_api(db: Session, brand_scope: str | None) -> dict:
    rows = list_drafts(db)
    return {"items": rows}


def draft_detail_api(db: Session, draft_id: str, brand_scope: str | None) -> dict:
    draft = get_draft(db, draft_id)
    payload = serialize_draft(draft)
    payload["live"] = _rule_rows_from_db(db)
    payload["diff"] = diff(db, draft, brand_scope=brand_scope) if draft.status != "published" else draft.last_trial
    return payload


def _rule_rows_from_db(db: Session) -> list[dict]:
    groups, rules = live_rules(db)
    return _rule_rows(groups, rules)


def diff_api(db: Session, draft_id: str, brand_scope: str | None) -> dict:
    draft = get_draft(db, draft_id)
    result = diff(db, draft, brand_scope=brand_scope)
    draft.last_trial = result
    db.commit()
    return result


def trial_unsaved(db: Session, payload, brand_scope: str | None) -> dict:
    """未保存草稿的直接对照：不落库，返回临时快照 + 同一批候选差异。"""
    base = baseline_digest(db)
    items = build_draft_items(db, _overrides(payload.changes))
    result = diff_against(db, items, base, brand_scope=brand_scope, limit=60)
    return result


def publish_api(db: Session, draft_id: str, reason: str, user: User, trace_id: str = "") -> dict:
    draft = get_draft(db, draft_id)
    try:
        result = publish(db, draft, user_email=user.email, reason=reason, trace_id=trace_id)
    except AppError as exc:
        db.rollback()
        record_publish_log(
            db,
            draft=draft,
            content_hash=draft.content_hash,
            outcome="failed",
            outcome_text=exc.detail or exc.message,
            versions=None,
            actor_email=user.email,
            trace_id=trace_id,
        )
        audit_service.record(
            db,
            action=ACTION_RULESET_PUBLISH_FAILED,
            actor=user,
            entity_type="ruleset_draft",
            entity_id=draft.id,
            detail={"code": exc.code, "detail": exc.detail},
            trace_id=trace_id,
        )
        db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - 发布失败必须保留原版本
        db.rollback()
        record_publish_log(
            db,
            draft=draft,
            content_hash=draft.content_hash,
            outcome="failed",
            outcome_text=str(exc),
            versions=None,
            actor_email=user.email,
            trace_id=trace_id,
        )
        db.commit()
        raise AppError(codes.RULESET_PUBLISH_FAILED, "规则发布失败，已保留原版本", str(exc)) from exc

    record_publish_log(
        db,
        draft=draft,
        content_hash=draft.content_hash,
        outcome="published",
        outcome_text="发布成功",
        versions=result.get("versions"),
        actor_email=user.email,
        trace_id=trace_id,
    )
    audit_service.record(
        db,
        action=ACTION_RULESET_PUBLISH,
        actor=user,
        entity_type="ruleset_draft",
        entity_id=draft.id,
        detail={
            "versions": result.get("versions"),
            "unchanged": result.get("unchanged_versions"),
            "eval_day": result.get("eval_day"),
            "reason": reason[:120],
            "idempotent": result.get("idempotent"),
        },
        trace_id=trace_id,
    )
    db.commit()
    return {**result, "draft": serialize_draft(draft), "overview": overview(db, None)}


def history_api(db: Session, brand_scope: str | None) -> dict:
    groups = {str(g.id): g for g in db.execute(select(KeywordGroup)).scalars().all()}
    rows = db.execute(
        select(RulesetVersion).order_by(RulesetVersion.started_at.desc(), RulesetVersion.version.desc()).limit(200)
    ).scalars().all()
    items = []
    for row in rows:
        group = groups.get(str(row.group_id))
        if group is None:
            continue
        if brand_scope and group.brand_key != brand_scope:
            continue
        payload = serialize_version(row)
        payload["code"] = group.code
        payload["name"] = group.name
        payload["brand_key"] = group.brand_key
        items.append(payload)
    logs = db.execute(
        select(RulesetPublishLog).order_by(RulesetPublishLog.created_at.desc()).limit(40)
    ).scalars().all()
    return {
        "items": items,
        "publishes": [
            {
                "id": str(log.id),
                "draft_id": str(log.draft_id) if log.draft_id else None,
                "content_hash": log.content_hash,
                "outcome": log.outcome,
                "outcome_text": log.outcome_text,
                "versions": log.versions,
                "actor_email": log.actor_email,
                "trace_id": log.trace_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "current_digest": baseline_digest(db),
    }


def _attribution_index(db: Session, entries: list[dict]) -> dict[str, dict]:
    """库内提及的「线上实际归属」（读归属表，不重算）：对照的 old 侧以此为准。"""
    ids = [to_uuid(entry["mention_id"]) for entry in entries if entry.get("mention_id")]
    if not ids:
        return {}
    rows = db.execute(
        select(MentionRuleAttribution, Mention.external_id)
        .join(Mention, Mention.id == MentionRuleAttribution.mention_id)
        .where(MentionRuleAttribution.mention_id.in_(ids))
    ).all()
    out: dict[str, dict] = {}
    for row, external_id in rows:
        if row.kept:
            out[external_id] = {
                "reason": row.reason if row.reason in ("matched", "retrieved", "seed") else "matched",
                "group_id": str(row.group_id),
                "hit_keywords": list(row.matched_keywords or []),
                "hit_synonyms": list(row.matched_synonyms or []),
                "hit_excludes": list(row.hit_excludes or []),
            }
        elif row.reason == "excluded":
            out[external_id] = {
                "reason": "excluded",
                "group_id": None,
                "hit_keywords": [],
                "hit_synonyms": [],
                "hit_excludes": list(row.hit_excludes or []),
            }
        else:
            out[external_id] = {
                "reason": "not_matched",
                "group_id": None,
                "hit_keywords": [],
                "hit_synonyms": [],
                "hit_excludes": [],
            }
    return out


def seed_attributions(db: Session) -> int:
    """seed 用：把已入库提及按其 seed 计划归属写入归属表（不按文本重算，保持演示基线 900/5）。"""
    versions = version_map(db)
    created = 0
    for mention in db.execute(select(Mention)).scalars().all():
        exists = db.execute(
            select(MentionRuleAttribution.id).where(MentionRuleAttribution.mention_id == mention.id)
        ).scalar_one_or_none()
        if exists is not None:
            continue
        version = versions.get(str(mention.group_id))
        db.add(
            MentionRuleAttribution(
                mention_id=mention.id,
                group_id=mention.group_id,
                ruleset_version_id=version.id if version else None,
                kept=True,
                matched_keywords=[],
                matched_synonyms=[],
                hit_excludes=[],
                reason="seed",
            )
        )
        created += 1
    db.flush()
    return created
