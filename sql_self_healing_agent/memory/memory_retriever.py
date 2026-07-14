from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot


class MemoryRetriever:
    def retrieve(self, diagnosis: DiagnosisResult, failed_sql: str, metadata_snapshot: MetadataSnapshot | None) -> MemoryRetrievalResult:
        return MemoryRetrievalResult(retrieved=[])
