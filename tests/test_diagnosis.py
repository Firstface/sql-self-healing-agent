import json
import unittest
from pathlib import Path

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.diagnostics.diagnosis_fusion import DiagnosisFusion
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput
from sql_self_healing_agent.diagnostics.llm_diagnoser import LLMDiagnoser
from sql_self_healing_agent.diagnostics.rule_classifier import RuleClassifier
from sql_self_healing_agent.logs.log_models import LogDigest
from tests.fakes import FakeLLMClient
from tests.llm_test_adapter import build_test_llm_adapter


class DiagnosisTest(unittest.TestCase):
    def test_keywords_remain_in_vocab(self) -> None:
        vocab = json.loads((Path(__file__).parents[1] / "sql_self_healing_agent/logs/keyword_vocab.json").read_text())
        diagnosis_input = DiagnosisInput(failed_sql="SELECT pay_amt FROM dwd_order_detail", log_digest=LogDigest(log_readable=True, matched_categories=["COLUMN_NOT_FOUND"], suspected_engine_error="Invalid column reference pay_amt"), keyword_vocab=vocab, allowed_error_types=[item.value for item in DiagnosedErrorType])
        rule = RuleClassifier().classify(diagnosis_input)
        llm = LLMDiagnoser((client := FakeLLMClient()), build_test_llm_adapter(client)).diagnose(diagnosis_input)
        result = DiagnosisFusion().fuse(diagnosis_input, rule, llm)
        self.assertEqual(result.diagnosed_error_type, DiagnosedErrorType.COLUMN_NOT_FOUND)
        self.assertEqual(result.primary_entity, "pay_amt")
        self.assertTrue(set(result.diagnosed_keywords).issubset(set(vocab["COLUMN_NOT_FOUND"])))

    def test_type_mismatch_extracts_column_after_cannot_compare(self) -> None:
        vocab = json.loads((Path(__file__).parents[1] / "sql_self_healing_agent/logs/keyword_vocab.json").read_text())
        diagnosis_input = DiagnosisInput(
            failed_sql="SELECT payment_amount FROM dwd_order_detail",
            log_digest=LogDigest(
                log_readable=True,
                matched_categories=["TYPE_MISMATCH"],
                suspected_engine_error="Cannot compare payment_amount string and bigint",
                root_cause_summary="Cannot compare payment_amount string and bigint",
            ),
            keyword_vocab=vocab,
            allowed_error_types=[item.value for item in DiagnosedErrorType],
        )
        rule = RuleClassifier().classify(diagnosis_input)
        result = DiagnosisFusion().fuse(diagnosis_input, rule, None)
        self.assertEqual(result.diagnosed_error_type, DiagnosedErrorType.TYPE_MISMATCH)
        self.assertEqual(result.primary_entity, "payment_amount")

    def test_type_mismatch_extracts_column_after_cannot_cast(self) -> None:
        self.assertEqual(
            DiagnosisFusion._entity("Cannot cast payment_amount string to bigint"),
            "payment_amount",
        )
