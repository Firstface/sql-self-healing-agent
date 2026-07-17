class ContextCompressionHook:
    def should_compact(self, operation_type: str) -> bool:
        return operation_type != "CONTEXT_COMPACTION"
