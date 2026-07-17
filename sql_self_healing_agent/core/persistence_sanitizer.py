import re


class PersistenceSanitizationError(ValueError):
    pass


class PersistenceSanitizer:
    """Fail-closed sanitizer for Artifact and Trace persistence."""

    _patterns = (
        (r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1<REDACTED>"),
        (r"(?i)\b(ark_api_key|api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=<REDACTED>"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <REDACTED>"),
        (r"\bsk-[A-Za-z0-9_-]{8,}\b", "<REDACTED_API_KEY>"),
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<EMAIL>"),
        (r"(?i)([?&](?:token|access_token|auth)=)[^&\s]+", r"\1<REDACTED>"),
    )
    _forbidden_after_sanitization = (r"\bsk-[A-Za-z0-9_-]{8,}\b",)

    def sanitize(self, content: str) -> str:
        if not isinstance(content, str):
            raise PersistenceSanitizationError("persistence content must be text")
        sanitized = content
        try:
            for pattern, replacement in self._patterns:
                sanitized = re.sub(pattern, replacement, sanitized)
        except re.error as exc:
            raise PersistenceSanitizationError("sanitization failed") from exc
        if any(re.search(pattern, sanitized) for pattern in self._forbidden_after_sanitization):
            raise PersistenceSanitizationError("sensitive content remains after sanitization")
        return sanitized
