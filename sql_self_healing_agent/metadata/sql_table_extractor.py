import re

import sqlglot
from sqlglot import exp

from sql_self_healing_agent.metadata.metadata_models import SQLTableExtractionResult, SQLTableRef


class SQLTableExtractor:
    def extract(self, sql: str) -> SQLTableExtractionResult:
        try:
            tree = sqlglot.parse_one(sql, read="hive")
            ctes = [cte.alias_or_name for cte in tree.find_all(exp.CTE)]
            cte_names = {name.casefold() for name in ctes}
            insert_target = tree.this if isinstance(tree, exp.Insert) else None
            tables: list[SQLTableRef] = []
            for table in tree.find_all(exp.Table):
                name = table.sql(dialect="hive")
                normalized = table.name.casefold()
                if normalized in cte_names:
                    continue
                source = "INSERT_TARGET" if insert_target is table else "FROM"
                parent = table.parent
                if isinstance(parent, exp.Join):
                    source = "JOIN"
                tables.append(SQLTableRef(raw_name=name, normalized_name=normalized, alias=table.alias or None, source_clause=source))
            return SQLTableExtractionResult(tables=tables, ctes=ctes, parse_success=True)
        except sqlglot.errors.SqlglotError as error:
            tables: list[SQLTableRef] = []
            for match in re.finditer(r"(?i)\b(FROM|JOIN|INSERT\s+(?:INTO|OVERWRITE)(?:\s+TABLE)?)\s+([`\w.]+)", sql):
                clause = match.group(1).upper()
                source = "INSERT_TARGET" if clause.startswith("INSERT") else clause
                raw = match.group(2).strip("`")
                tables.append(SQLTableRef(raw_name=raw, normalized_name=raw.casefold(), source_clause=source))
            return SQLTableExtractionResult(tables=tables, parse_success=False, parse_error=str(error))
