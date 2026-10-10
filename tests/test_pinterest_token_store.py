import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pinterest import pinterest_pins


class PinterestTokenStoreTests(unittest.TestCase):
    def test_bootstraps_token_file_from_environment(self):
        tokens = {"environments": {"production": {"access_token": "test-token"}}}
        encoded_tokens = base64.b64encode(json.dumps(tokens).encode()).decode()

        with tempfile.TemporaryDirectory() as directory:
            token_path = os.path.join(directory, "persistent", "pinterest_tokens.json")
            with patch.object(pinterest_pins, "TOKEN_FILE", token_path):
                with patch.dict(os.environ, {"PINTEREST_TOKENS_JSON_B64": encoded_tokens}):
                    self.assertEqual(pinterest_pins._load_tokens(), tokens)

                with open(token_path, encoding="utf-8") as token_file:
                    self.assertEqual(json.load(token_file), tokens)


if __name__ == "__main__":
    unittest.main()