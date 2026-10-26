import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oilmon import config, ingest


class RawStoreTests(unittest.TestCase):
    def test_redownload_skipped_but_duplicate_within_a_pull_kept(self):
        a, b = {"sample_id": "S1", "updated_at": "1"}, {"sample_id": "S2", "updated_at": "1"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "RAW_DIR", Path(tmp)):
            Path(tmp, "pull_1.json").write_text(json.dumps([a, b, b]))   # lab sent S2 twice
            Path(tmp, "pull_2.json").write_text(json.dumps([a, b]))      # we re-downloaded both
            records = ingest.load_raw_store()
        self.assertEqual([r["sample_id"] for r in records], ["S1", "S2", "S2"])


if __name__ == "__main__":
    unittest.main()
