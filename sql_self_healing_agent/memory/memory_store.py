import json
import os
import uuid
from pathlib import Path

from sql_self_healing_agent.core.atomic_io import read_json, write_json_atomic, write_text_atomic
from sql_self_healing_agent.memory.memory_models import ExperienceFrontmatter


class MemoryStore:
    def __init__(self, base_dir: Path | str = Path(".memory")) -> None:
        self.base_dir = Path(base_dir)
        self.experiences_dir = self.base_dir / "experiences"
        self.index_path = self.base_dir / "index" / "keyword_index.json"

    def experience_path(self, experience_id: str) -> Path:
        return self.experiences_dir / f"{experience_id}.md"

    def list_experience_ids(self) -> list[str]:
        return [path.stem for path in sorted(self.experiences_dir.glob("*.md"))] if self.experiences_dir.exists() else []

    def read_frontmatter(self, experience_id: str) -> ExperienceFrontmatter:
        text = self.experience_path(experience_id).read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError("missing frontmatter")
        end = lines.index("---", 1)
        keywords: list[str] = []
        description: str | None = None
        in_keywords = False
        for line in lines[1:end]:
            if line == "keyword:":
                in_keywords = True
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
                in_keywords = False
            elif in_keywords and line.strip().startswith("- "):
                keywords.append(line.strip()[2:].strip())
            elif line.strip():
                raise ValueError("unsupported frontmatter field")
        return ExperienceFrontmatter(keyword=keywords, description=description or "")

    def read_body(self, experience_id: str) -> str:
        text = self.experience_path(experience_id).read_text(encoding="utf-8")
        parts = text.split("---", 2)
        return parts[2].lstrip() if len(parts) == 3 else ""

    def load_index(self) -> dict[str, list[str]]:
        if not self.index_path.exists():
            return {}
        payload = read_json(self.index_path)
        return {str(key): list(dict.fromkeys(value)) for key, value in payload.items()}

    def rebuild_index(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for experience_id in self.list_experience_ids():
            frontmatter = self.read_frontmatter(experience_id)
            for keyword in frontmatter.keyword:
                index.setdefault(keyword, []).append(experience_id)
        index = {key: sorted(set(value)) for key, value in sorted(index.items())}
        write_json_atomic(self.index_path, index)
        return index

    def save_markdown(self, experience_id: str, content: str) -> Path:
        write_text_atomic(self.experience_path(experience_id), content)
        self.rebuild_index()
        return self.experience_path(experience_id)

    def find_by_logical_key(self, session_id: str, attempt_id: str) -> str | None:
        marker = f"logical-key: {session_id}:{attempt_id}"
        for experience_id in self.list_experience_ids():
            if marker in self.read_body(experience_id):
                return experience_id
        return None
