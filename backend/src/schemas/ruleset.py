"""规则版本请求模型：草稿（逐组覆盖）/ 对照 / 发布。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.schemas.group import _clean_words


class RulePatch(BaseModel):
    group_id: str
    keywords: list[str] | None = None
    synonyms: list[str] | None = None
    excludes: list[str] | None = None
    z_threshold: float | None = Field(default=None, ge=1.0, le=8.0)
    min_count: int | None = Field(default=None, ge=1, le=50)
    enabled: bool | None = None

    @field_validator("keywords", "synonyms", "excludes")
    @classmethod
    def clean_words(cls, value: list[str] | None) -> list[str] | None:
        return _clean_words(value) if value is not None else None


class DraftRequest(BaseModel):
    label: str = Field(default="", max_length=120)
    changes: list[RulePatch] = Field(default_factory=list)
    note: str = Field(default="", max_length=400)


class PublishRequest(BaseModel):
    reason: str = Field(default="", max_length=400)


class TrialRequest(BaseModel):
    """直接对照一份「未保存的覆盖」：便于前端在草稿详情页里再试算一次。"""
    changes: list[RulePatch] = Field(default_factory=list)
    brand_key: str | None = None
    limit: int = Field(default=60, ge=10, le=200)
