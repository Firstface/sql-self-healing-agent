import json
from pathlib import Path

from sql_self_healing_agent.metadata.metadata_models import ColumnCandidate, ColumnMetadata, TableMetadata
from sql_self_healing_agent.metadata.metadata_provider import MetadataProvider


def normalize_name(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[right_index] + 1, previous[right_index - 1] + (left_character != right_character)))
        previous = current
    return previous[-1]


class MockMetadataProvider(MetadataProvider):
    def __init__(self, metadata_path: Path | str = Path("mocks/metadata/tables.json")) -> None:
        self.metadata_path = Path(metadata_path)
        self.payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def get_table_metadata(self, table_name: str) -> TableMetadata | None:
        raw = self.payload.get(table_name) or self.payload.get(table_name.casefold())
        if raw is None or not raw.get("exists", True):
            return None
        columns = [ColumnMetadata(name=item["name"], normalized_name=normalize_name(item["name"]), data_type=item.get("data_type"), comment=item.get("comment"), is_partition=item.get("is_partition", False)) for item in raw.get("columns", [])]
        return TableMetadata(table_name=table_name, normalized_table_name=table_name.casefold(), exists=True, columns=columns, partition_columns=[item for item in columns if item.is_partition], owner=raw.get("owner"), source="MOCK")

    def find_column_candidates(self, original_name: str, tables: list[TableMetadata]) -> list[ColumnCandidate]:
        original = normalize_name(original_name)
        candidates: list[ColumnCandidate] = []
        for table in tables:
            for column in table.columns:
                normalized = normalize_name(column.name)
                distance = edit_distance(original, normalized)
                score = 1.0 if original == normalized else 1.0 - distance / max(len(original), len(normalized), 1)
                source = "NORMALIZED" if original == normalized else "EDIT_DISTANCE"
                reason = "normalized name match" if original == normalized else f"edit distance {distance}"
                candidates.append(ColumnCandidate(original_name=original_name, candidate_name=column.name, table_name=table.table_name, data_type=column.data_type, score=score, reason=reason, source=source))
        return sorted(candidates, key=lambda item: item.score, reverse=True)
