from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperienceFrontmatter(StrictModel):
    keyword: list[str]
    description: str


class ExperienceSummary(StrictModel):
    experience_id: str
    keyword: list[str]
    description: str
    matched_by: list[str] = Field(default_factory=list)
    artifact_ref: str | None = None


class MemoryRetrievalResult(StrictModel):
    matched: bool
    matched_experiences: list[ExperienceSummary] = Field(default_factory=list)
    scanned_count: int
    discarded_count: int
    artifact_ref: str | None = None


class ConfirmedExperienceInput(StrictModel):
    session_id: str
    attempt_id: str
    original_sql: str
    confirmed_sql: str
    diagnosed_keywords: list[str]
    description: str
    modification_summary: str
    error_summary: str = ""
