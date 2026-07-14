import unittest
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
class MetadataTest(unittest.TestCase):
 def test_mock_metadata(self):
  table=MockMetadataProvider().get_table_metadata('dwd_order_detail')
  self.assertIn('payment_amount',[c.name for c in table.columns])
