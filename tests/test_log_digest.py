import json, tempfile, unittest
from pathlib import Path
from sql_self_healing_agent.logs.log_compressor import LogCompressor
class LogDigestTest(unittest.TestCase):
 def test_extracts_column_error(self):
  vocab=json.loads(__import__('pathlib').Path('sql_self_healing_agent/logs/keyword_vocab.json').read_text())
  digest=LogCompressor().build_digest('mocks/logs/task_123_round_1.log',None,vocab)
  self.assertTrue(digest.log_readable); self.assertIn('COLUMN_NOT_FOUND',digest.matched_categories)
 def test_extracts_yarn_am_infrastructure_error(self):
  vocab=json.loads(Path('sql_self_healing_agent/logs/keyword_vocab.json').read_text())
  with tempfile.NamedTemporaryFile('w',suffix='.log',delete=False) as fh:
   fh.write('26/07/22 09:15:53 INFO some noise\n')
   fh.write('error diagnostics: {"exitCode":1,"errorMessage":"AM Container for appattempt_1_1 exited with exitCode: -21003. Container expired since it was unused. Failing this attempt."}\n')
   log_path=fh.name
  digest=LogCompressor().build_digest(log_path,None,vocab)
  self.assertIn('INFRASTRUCTURE_ERROR',digest.matched_categories)
