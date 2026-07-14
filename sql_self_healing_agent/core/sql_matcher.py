import re

import sqlglot


class SQLMatcher:
    @staticmethod
    def _fallback_normalize(sql: str) -> str:
        without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        without_line_comments = re.sub(r"--[^\n]*", " ", without_block_comments)
        without_semicolon = without_line_comments.strip().rstrip(";").strip()
        return re.sub(r"\s+", " ", without_semicolon).casefold()

    def normalize(self, sql: str) -> str:
        fallback = self._fallback_normalize(sql)
        try:
            return sqlglot.parse_one(fallback).sql(normalize=True, pretty=False)
        except sqlglot.errors.SqlglotError:
            return fallback

    def match(self, left: str, right: str) -> bool:
        return self.normalize(left) == self.normalize(right)
