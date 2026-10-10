import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest import pinterest_pins  # noqa: E402


class PinterestMediaUploadTests(unittest.TestCase):
    @patch("pinterest.pinterest_pins.requests.post")
    @patch("pinterest.pinterest_pins.requests.put")
    def test_upload_image_bytes_returns_media_id(self, mock_put, mock_post):
        mock_post.return_value.ok = True
        mock_post.return_value.status_code = 200
        mock_post.return_value.headers = {}
        mock_post.return_value.json.return_value = {
            "media_id": "abc123",
            "upload_url": "https://upload.example/media",
        }

        mock_put.return_value.ok = True
        mock_put.return_value.status_code = 200
        mock_put.return_value.headers = {}
        mock_put.return_value.text = ""

        result = pinterest_pins.upload_image_bytes(b"fake-image-bytes", "test.png", "image/png")

        self.assertEqual(result, "abc123")
        mock_put.assert_called_once_with(
            "https://upload.example/media",
            headers={"Content-Type": "image/png"},
            data=b"fake-image-bytes",
        )

    @patch("pinterest.pinterest_pins.upload_image_bytes")
    @patch("pinterest.pinterest_pins.requests.post")
    def test_create_pin_with_uploaded_media_uses_media_id(self, mock_post, mock_upload):
        mock_upload.return_value = "media-456"

        class FakeResponse:
            ok = True
            status_code = 201
            text = ""
            headers = {}

            def json(self):
                return {"id": "pin-999", "url": "https://example.com/pin/999"}

        mock_post.return_value = FakeResponse()

        result = pinterest_pins.create_pin(
            board_id="board-1",
            title="Test",
            description="Test pin",
            link="https://example.com",
            image_url=None,
            image_bytes=b"raw-bytes",
            board_section_id=None,
        )

        self.assertEqual(result["id"], "pin-999")
        mock_upload.assert_called_once_with(b"raw-bytes", filename="pin.png", content_type="image/png")


if __name__ == "__main__":
    unittest.main()
