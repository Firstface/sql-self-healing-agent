from pathlib import Path

from sql_self_healing_agent.memory.keyword_list import KeywordList
from sql_self_healing_agent.memory.memory_models import ExperienceSummary, MemoryRetrievalResult
from sql_self_healing_agent.memory.memory_store import MemoryStore


class MemoryRetriever:
    def __init__(self, base_dir: Path | str = Path(".memory"), keyword_list: KeywordList | None = None, max_context_hits: int = 5) -> None:
        self.store = MemoryStore(base_dir)
        self.keyword_list = keyword_list or KeywordList()
        self.max_context_hits = max_context_hits

    def retrieve_keywords(self, diagnosed_keywords: list[str], query_summary: str | None = None, limit: int | None = None) -> MemoryRetrievalResult:
        keywords = self.keyword_list.normalize(diagnosed_keywords)
        index = self.store.load_index()
        all_ids = self.store.list_experience_ids()
        unknown = "unknown" in keywords
        candidate_ids = all_ids if unknown else sorted({experience_id for keyword in keywords for experience_id in index.get(keyword, [])})
        matched: list[ExperienceSummary] = []
        scanned = 0
        query = (query_summary or "").casefold()
        for experience_id in candidate_ids:
            scanned += 1
            frontmatter = self.store.read_frontmatter(experience_id)
            matched_by = [keyword for keyword in keywords if keyword in frontmatter.keyword]
            relevant = bool(matched_by)
            if unknown:
                relevant = not query or any(term in (frontmatter.description + " " + self.store.read_body(experience_id)).casefold() for term in query.split() if len(term) > 2)
            if relevant:
                matched.append(ExperienceSummary(experience_id=experience_id, keyword=frontmatter.keyword, description=frontmatter.description, matched_by=matched_by or ["unknown"], artifact_ref=f"artifact://memory/{experience_id}"))
        final = matched[: min(limit or self.max_context_hits, self.max_context_hits)]
        return MemoryRetrievalResult(matched=bool(final), matched_experiences=final, scanned_count=scanned, discarded_count=scanned - len(final))
