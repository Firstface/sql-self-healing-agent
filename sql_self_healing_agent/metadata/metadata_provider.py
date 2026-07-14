from abc import ABC, abstractmethod

from sql_self_healing_agent.metadata.metadata_models import TableMetadata


class MetadataProvider(ABC):
    @abstractmethod
    def get_table_metadata(self, table_name: str) -> TableMetadata | None:
        ...
