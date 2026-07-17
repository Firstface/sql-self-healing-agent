import unittest

from sql_self_healing_agent.llm.llm_client import LLMClientError, LLMErrorType
from sql_self_healing_agent.llm.retry_hook import LLMRetryHook


class LLMRetryHookTest(unittest.TestCase):
    def test_schema_and_transient_budgets_are_independent(self) -> None:
        sequence = [LLMErrorType.SCHEMA_ERROR, LLMErrorType.TRANSIENT_ERROR, LLMErrorType.TRANSIENT_ERROR]
        calls = []
        def operation(feedback):
            calls.append(feedback)
            if sequence:
                raise LLMClientError(sequence.pop(0), "safe")
            return "ok"
        self.assertEqual(LLMRetryHook().run(operation), "ok")
        self.assertEqual(len(calls), 4)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])

    def test_auth_and_invalid_request_never_retry(self) -> None:
        for error_type in (LLMErrorType.AUTH_ERROR, LLMErrorType.INVALID_REQUEST):
            calls = []
            def operation(feedback):
                calls.append(feedback)
                raise LLMClientError(error_type, "safe")
            with self.assertRaises(LLMClientError):
                LLMRetryHook().run(operation)
            self.assertEqual(len(calls), 1)
