import json, unittest
from sql_self_healing_agent.logs.log_compressor import LogCompressor
class LogDigestTest(unittest.TestCase):
 def test_extracts_column_error(self):
  vocab=json.loads(__import__('pathlib').Path('sql_self_healing_agent/logs/keyword_vocab.json').read_text())
  digest=LogCompressor().build_digest('mocks/logs/task_123_round_1.log',None,vocab)
  self.assertTrue(digest.log_readable); self.assertIn('COLUMN_NOT_FOUND',digest.matched_categories)
