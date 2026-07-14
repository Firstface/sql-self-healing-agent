from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SQLTableRef(StrictModel):
    raw_name: str
    normalized_name: str
    alias: str | None = None
    source_clause: Literal["FROM", "JOIN", "INSERT_TARGET", "CTE", "UNKNOWN"]


class SQLTableExtractionResult(StrictModel):
    tables: list[SQLTableRef]
    ctes: list[str] = Field(default_factory=list)
    parse_success: bool
    parse_error: str | None = None


class ColumnMetadata(StrictModel):
    name: str
    normalized_name: str
    data_type: str | None = None
    comment: str | None = None
    is_partition: bool = False


class TableMetadata(StrictModel):
    table_name: str
    normalized_table_name: str
    exists: bool
    columns: list[ColumnMetadata] = Field(default_factory=list)
    partition_columns: list[ColumnMetadata] = Field(default_factory=list)
    owner: str | None = None
    source: Literal["MOCK", "REAL", "UNKNOWN"] = "UNKNOWN"
    error: str | None = None


class MetadataSnapshot(StrictModel):
    extraction_result: SQLTableExtractionResult
    tables: list[TableMetadata]
    missing_tables: list[str] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)
    created_at: str


class ColumnCandidate(StrictModel):
    original_name: str
    candidate_name: str
    table_name: str
    data_type: str | None = None
    score: float
    reason: str
    source: Literal["EXACT", "NORMALIZED", "EDIT_DISTANCE", "COMMENT", "MEMORY"]
