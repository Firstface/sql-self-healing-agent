from pydantic import BaseModel, ConfigDict, Field


class RetrievedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_id: str
    score: float
    match_reasons: list[str]
    experience: dict


class MemoryRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retrieved: list[RetrievedExperience]
    fingerprint_matches: list[str] = Field(default_factory=list)
    keyword_matches: list[str] = Field(default_factory=list)
