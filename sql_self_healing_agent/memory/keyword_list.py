import json
from pathlib import Path


class KeywordList:
    def __init__(self, path: Path | str | None = None) -> None:
        source = Path(path) if path else Path(__file__).parents[1] / "logs" / "keyword_vocab.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        self.values = {keyword for keywords in payload.values() for keyword in keywords}
        self.values.add("unknown")
        assert "column_not_found" in self.values
        assert "COLUMN_NOT_FOUND" not in self.values

    def normalize(self, keywords: list[str]) -> list[str]:
        valid = list(dict.fromkeys(keyword for keyword in keywords if keyword in self.values))
        return valid or ["unknown"]
