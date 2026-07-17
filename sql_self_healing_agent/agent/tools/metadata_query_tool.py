from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.metadata.sql_table_extractor import SQLTableExtractor


class MetadataQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    table_names: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    include_partitions: bool = True


class MetadataQueryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["AVAILABLE", "PARTIAL", "FAILED"]
    summary: str | None = None
    metadata_snapshot_ref: str | None = None
    missing_tables: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list)
    partition_info_available: bool = False


class MetadataQueryTool:
    name = "MetadataQueryTool"
    description = "查询受控元数据，不访问生产 SQL 执行器"
    input_model = MetadataQueryInput
    output_model = MetadataQueryOutput
    allowed_phases = {"DIAGNOSING", "PLANNING", "GENERATING"}
    max_output_tokens = 1500
    produces_artifact = False

    def __init__(self, provider: MockMetadataProvider) -> None:
        self.provider = provider
        self.extractor = SQLTableExtractor()

    def run(self, context: AgentContext, input_data: MetadataQueryInput) -> MetadataQueryOutput:
        names = input_data.table_names or [item.normalized_name for item in self.extractor.extract(input_data.sql).tables]
        tables, missing = [], []
        for name in names:
            metadata = self.provider.get_table_metadata(name)
            if metadata is None:
                missing.append(name)
            else:
                tables.append(metadata)
        available_columns = {column.name for table in tables for column in table.columns}
        missing_columns = [column for column in input_data.columns if column not in available_columns]
        status = "AVAILABLE" if tables and not missing and not missing_columns else "PARTIAL" if tables else "FAILED"
        return MetadataQueryOutput(status=status, summary=f"tables={len(tables)}, columns={len(available_columns)}", missing_tables=missing, missing_columns=missing_columns, partition_info_available=any(table.partition_columns for table in tables))
