"""规则版本：草稿/线上并存 · 同批候选对照 · 一次性发布 · 失败保留 · 幂等。

测试用一个内存 SQLite 驱动同一份 `Base.metadata`（Postgres 之外的确定性替身），
覆盖的是服务层口径，不是 SQL 方言：
- 草稿不改变任何线上口径；
- 新旧对照用同一批候选，给出新增/漏掉/被排除/换组与情感、爆发差异；
- 发布是全有或全无，失败时线上版本不变；
- 同一草稿重复发布不新建版本、不重复统计；
- 采集、提及归属、情感汇总、爆发预警读同一份发布口径，历史预警保留原版本。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from scripts import fixtures
from src.models import Base
from src.models.burst_alert import BurstAlert
from src.models.keyword_group import KeywordGroup
from src.models.mention import Mention
from src.models.rule_version import MentionRuleAttribution, RulesetDraft, RulesetVersion
from src.models.sentiment_score import SentimentScore
from src.models.user import User
from src.services import burst_service, ruleset_service, sentiment_service
from src.services.analysis.sentiment_analyzer import analyze
from src.services.fixtures_loader import inbox_corpus
from src.utils.timeutil import TZ_SHANGHAI, utcnow


@compiles(PGUUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):  # pragma: no cover - 驱动适配
    return "CHAR(36)"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in Base.metadata.tables.values():
        table.schema = None
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed(db: Session) -> tuple[dict[str, KeywordGroup], User]:
    admin = User(email="admin@newssweep.io", password_hash="x", display_name="admin", role="admin")
    db.add(admin)
    db.flush()
    groups: dict[str, KeywordGroup] = {}
    for spec in fixtures.GROUP_SPECS:
        group = KeywordGroup(
            code=spec["code"],
            name=spec["name"],
            brand_key=spec["brand_key"],
            keywords=list(spec["keywords"]),
            synonyms=list(spec["synonyms"]),
            excludes=list(spec["excludes"]),
            enabled=True,
            priority=spec["priority"],
            z_threshold=spec["z_threshold"],
            min_count=spec["min_count"],
            owner_note=spec["owner_note"],
        )
        db.add(group)
        db.flush()
        groups[spec["code"]] = group
    for item in fixtures.seed_mentions():
        group = groups[item["group_code"]]
        mention = Mention(
            external_id=item["external_id"],
            group_id=group.id,
            brand_key=group.brand_key,
            source_code=item["source_code"],
            title=item["title"],
            body=item["body"],
            author=item["author"],
            author_followers=item["author_followers"],
            url=item["url"],
            engagement=item["engagement"],
            published_at=item["published_at"],
            fetched_at=item["published_at"],
            content_hash=item["content_hash"],
        )
        db.add(mention)
        db.flush()
        result = analyze(f"{mention.title}\n{mention.body}")
        db.add(
            SentimentScore(
                mention_id=mention.id,
                score=result.score,
                label=result.label,
                final_label=result.label,
                confidence=result.confidence,
                hits=result.hits,
                positive_count=result.positive_count,
                negative_count=result.negative_count,
                negation_count=result.negation_count,
                degree_count=result.degree_count,
                rhetoric_count=result.rhetoric_count,
                exclaim_count=result.exclaim_count,
                algorithm_version=result.algorithm_version,
                review_status=item["review_status"],
            )
        )
    db.commit()
    ruleset_service.seed_versions(db, actor_email=admin.email)
    ruleset_service.seed_attributions(db)
    db.commit()
    burst_service.run_detection(db, admin, None)
    db.commit()
    return groups, admin


def _draft(db: Session, group: KeywordGroup, **rule) -> RulesetDraft:
    draft = ruleset_service.save_draft(
        db,
        [{"group_id": str(group.id), **rule}],
        label="测试草稿",
        group_id=None,
        user_email="admin@newssweep.io",
    )
    db.commit()
    return draft


# --------------------------------------------------------------- 草稿 ----


def test_draft_does_not_change_live_rules_or_attributions(db: Session) -> None:
    groups, _ = _seed(db)
    before_versions = {
        gid: row.version for gid, row in ruleset_service.current_versions(db).items()
    }
    before_kept = db.execute(
        select(MentionRuleAttribution.mention_id, MentionRuleAttribution.group_id).order_by(
            MentionRuleAttribution.mention_id
        )
    ).all()

    _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质"])

    after_versions = {gid: row.version for gid, row in ruleset_service.current_versions(db).items()}
    after_kept = db.execute(
        select(MentionRuleAttribution.mention_id, MentionRuleAttribution.group_id).order_by(
            MentionRuleAttribution.mention_id
        )
    ).all()
    assert before_versions == after_versions
    assert before_kept == after_kept


def test_same_draft_content_reuses_the_same_draft_record(db: Session) -> None:
    groups, _ = _seed(db)
    first = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质"])
    second = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质"])

    assert first.id == second.id
    assert db.execute(select(RulesetDraft)).scalars().all().__len__() == 1


# --------------------------------------------------------------- 对照 ----


def test_diff_uses_the_same_candidate_batch_for_old_and_new(db: Session) -> None:
    groups, _ = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质"])

    first = ruleset_service.diff(db, draft, limit=40)
    second = ruleset_service.diff(db, draft, limit=40)

    assert first["batch"] == second["batch"]
    assert [row["external_id"] for row in first["rows"]] == [row["external_id"] for row in second["rows"]]
    assert first["batch"]["total"] == len(first["rows"])
    assert first["compare"] == second["compare"]


def test_diff_reports_dropped_and_excluded_candidates(db: Session) -> None:
    groups, _ = _seed(db)
    disabled = _draft(db, groups["coffee-foreign"], enabled=False)
    dropped = ruleset_service.diff(db, disabled, limit=60)
    assert dropped["compare"]["dropped"] > 0
    assert dropped["trial"]["compare"]["dropped"] > 0

    excluded = _draft(db, groups["coffee-taste"], excludes=["价格", "客服"])
    result = ruleset_service.diff(db, excluded, limit=60)
    assert result["compare"]["excluded"] > 0
    excluded_rows = [row for row in result["rows"] if row["change"] == "excluded"]
    assert excluded_rows and all(row["new"]["excludes"] for row in excluded_rows)


def test_diff_reports_moved_candidates_and_evidence(db: Session) -> None:
    groups, _ = _seed(db)
    draft = _draft(db, groups["outdoor-durability"], keywords=["耐用", "开线", "断裂", "防水", "价格"])
    result = ruleset_service.diff(db, draft, limit=60)

    assert result["compare"]["moved"] > 0
    moved = [row for row in result["rows"] if row["change"] == "moved"]
    assert any(
        row["old"]["code"] == "outdoor-price" and row["new"]["code"] == "outdoor-durability"
        for row in moved
    )
    assert all(row["new"]["hits"] for row in moved)


def test_diff_reports_sentiment_and_burst_differences(db: Session) -> None:
    groups, _ = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], enabled=False, keywords=["异物"])
    result = ruleset_service.diff(db, draft, limit=60)

    assert set(result["sentiment"]["old"]) == {"pos", "neu", "neg"}
    assert sum(result["sentiment"]["old"].values()) >= sum(result["sentiment"]["new"].values())
    assert len(result["burst"]["old"]["events"]) > len(result["burst"]["new"]["events"])
    assert result["sentiment"]["changed_rows"]
    assert all("hits" in row["sentiment"] for row in result["sentiment"]["changed_rows"])


# --------------------------------------------------------------- 发布 ----


def test_publish_creates_one_version_per_changed_group_and_keeps_history(db: Session) -> None:
    groups, admin = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质", "塑料", "虫", "沉淀"])
    before = ruleset_service.current_versions(db)[str(groups["coffee-foreign"].id)].version

    result = ruleset_service.publish(db, draft, user_email=admin.email, reason="补充同义词")
    db.commit()

    assert result["new_versions"] == {"coffee-foreign": before + 1}
    assert result["versions"]["coffee-foreign"] == before + 1
    assert set(result["unchanged_versions"]) == set(groups) - {"coffee-foreign"}
    assert result["idempotent"] is False
    old = db.execute(
        select(RulesetVersion).where(
            RulesetVersion.group_id == groups["coffee-foreign"].id,
            RulesetVersion.version == before,
        )
    ).scalar_one()
    assert old.is_current is False and old.ended_at is not None
    current = ruleset_service.current_rule(db, str(groups["coffee-foreign"].id))
    assert current.version == before + 1 and current.is_current is True


def test_publish_is_idempotent_for_the_same_draft(db: Session) -> None:
    groups, admin = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质", "沉淀"])

    first = ruleset_service.publish(db, draft, user_email=admin.email)
    db.commit()
    versions_after_first = {
        gid: row.version for gid, row in ruleset_service.current_versions(db).items()
    }
    second = ruleset_service.publish(db, draft, user_email=admin.email)
    db.commit()
    versions_after_second = {
        gid: row.version for gid, row in ruleset_service.current_versions(db).items()
    }

    assert second["idempotent"] is True
    assert second["versions"] == first["versions"]
    assert second["new_versions"] == {} and second["unchanged_versions"]["coffee-foreign"] == first["versions"]["coffee-foreign"]
    assert versions_after_first == versions_after_second
    total_versions = db.execute(select(RulesetVersion)).scalars().all()
    assert len(total_versions) == len(fixtures.GROUP_SPECS) + 1  # 只有 coffee-foreign 多了一个版本


def test_publish_failure_keeps_previous_live_version(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    groups, admin = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质", "沉淀"])
    before = {gid: row.version for gid, row in ruleset_service.current_versions(db).items()}

    def boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(ruleset_service, "_apply_rule_to_group", boom)
    with pytest.raises(Exception):
        ruleset_service.publish_api(db, str(draft.id), "失败演练", admin)
    after = {gid: row.version for gid, row in ruleset_service.current_versions(db).items()}
    assert before == after
    live = ruleset_service.current_rule(db, str(groups["coffee-foreign"].id))
    assert live.version == before[str(groups["coffee-foreign"].id)]


def test_stale_draft_cannot_be_published_after_live_changes(db: Session) -> None:
    groups, admin = _seed(db)
    first = _draft(db, groups["coffee-foreign"], keywords=["异物", "杂质"])
    ruleset_service.publish(db, first, user_email=admin.email)
    db.commit()

    # 基于旧基线的另一份草稿（手动模拟发布前线上又被改过）
    stale = _draft(db, groups["coffee-taste"], keywords=["口感", "回甘"])
    stale.base_digest = "0" * 64
    db.commit()

    with pytest.raises(Exception) as exc:
        ruleset_service.publish(db, stale, user_email=admin.email)
    assert "线上版本" in str(exc.value) or "基准" in str(exc.value)


# ------------------------------------------------ 四环节同一口径 / 历史 ----


def test_all_four_stages_read_the_published_version(db: Session) -> None:
    groups, admin = _seed(db)
    draft = _draft(db, groups["coffee-foreign"], enabled=False)
    ruleset_service.publish(db, draft, user_email=admin.email)
    db.commit()

    burst_service.run_detection(db, admin, None)
    db.commit()

    alignment = ruleset_service.overview(db, None)["alignment"]
    assert alignment["stale_attributions"] == 0
    assert alignment["aligned"] is True

    listed = burst_service.list_alerts(db, None)
    assert all(
        item["ruleset_version"] == ruleset_service.current_rule(db, item["group_id"]).version
        for item in listed["items"]
    )
    summary = sentiment_service.summary(db, None)
    assert summary["total"] == alignment["kept"]


def test_historical_burst_alerts_keep_their_original_version(db: Session) -> None:
    groups, admin = _seed(db)
    before = {row.id: (row.ruleset_version, row.count) for row in db.execute(select(BurstAlert)).scalars().all()}
    assert before

    draft = _draft(db, groups["coffee-foreign"], z_threshold=8.0, keywords=["异物"])
    ruleset_service.publish(db, draft, user_email=admin.email)
    db.commit()
    burst_service.run_detection(db, admin, None)
    db.commit()

    after = {row.id: (row.ruleset_version, row.count) for row in db.execute(select(BurstAlert)).scalars().all()}
    for alert_id, snapshot in before.items():
        assert after[alert_id] == snapshot, "历史预警的版本与指标不能被新发布重写"
    archive = burst_service.versioned_alerts(db, None)
    versions = [item["version"] for item in archive["versions"]]
    assert 1 in versions
    assert max(versions) >= 1


# ------------------------------------------------------------- 采集口径 ----


def test_collection_uses_published_rules_and_writes_attributions(db: Session) -> None:
    groups, admin = _seed(db)
    draft = _draft(db, groups["coffee-taste"], enabled=False)
    ruleset_service.publish(db, draft, user_email=admin.email)
    db.commit()

    from src.services import collect_service

    before = int(db.execute(select(Mention.id)).scalars().all().__len__())
    report = collect_service.run_collection(db, admin)
    assert report["inserted"] > 0
    assert report["excluded"] >= 0
    total = int(db.execute(select(Mention.id)).scalars().all().__len__())
    assert total == before + report["inserted"] + report["excluded"]
    # 被排除的条目也留有归属行（kept=false），保证四环节可对账
    attributed = int(db.execute(select(MentionRuleAttribution.id)).scalars().all().__len__())
    assert attributed == total
