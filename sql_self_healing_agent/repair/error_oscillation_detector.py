from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisHistoryItem


class ErrorOscillationDetector:
    def detect(self, diagnosis_history: list[DiagnosisHistoryItem]) -> bool:
        fingerprints = [item.error_fingerprint for item in diagnosis_history]
        if len(fingerprints) >= 3:
            recent = fingerprints[-3:]
            if recent[0] == recent[2] and recent[0] != recent[1]:
                return True
        if len(fingerprints) >= 4:
            recent = fingerprints[-4:]
            if recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]:
                return True
        return False
