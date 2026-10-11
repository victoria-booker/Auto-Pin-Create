import base64
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest import pinterest_pins  # noqa: E402


def _created(pin_id="pin-999"):
    response = MagicMock(ok=True, status_code=201, headers={})
    response.json.return_value = {"id": pin_id}
    return response


@patch("pinterest.pinterest_pins._headers", return_value={})
class PinterestImageBytesTests(unittest.TestCase):
    @patch("pinterest.pinterest_pins.requests.post")
    def test_image_bytes_are_sent_inline_as_base64(self, mock_post, _headers):
        mock_post.return_value = _created()

        result = pinterest_pins.create_pin(
            board_id="board-1",
            title="Test",
            description="Test pin",
            link="https://example.com",
            image_bytes=b"raw-bytes",
            content_type="image/jpeg",
        )

        self.assertEqual(result["id"], "pin-999")
        # one call: straight to /pins, never the video-only /media endpoint
        mock_post.assert_called_once()
        url, kwargs = mock_post.call_args.args[0], mock_post.call_args.kwargs
        self.assertTrue(url.endswith("/pins"))
        self.assertEqual(
            kwargs["json"]["media_source"],
            {"source_type": "image_base64", "content_type": "image/jpeg", "data": base64.b64encode(b"raw-bytes").decode()},
        )

    @patch("pinterest.pinterest_pins.requests.post")
    def test_image_url_is_passed_through(self, mock_post, _headers):
        mock_post.return_value = _created()

        pinterest_pins.create_pin("board-1", "T", "D", "https://example.com", image_url="https://img.example/p.png")

        self.assertEqual(
            mock_post.call_args.kwargs["json"]["media_source"],
            {"source_type": "image_url", "url": "https://img.example/p.png"},
        )

    @patch("pinterest.pinterest_pins.requests.post")
    def test_rejects_image_types_pinterest_cannot_take_inline(self, mock_post, _headers):
        with self.assertRaises(ValueError):
            pinterest_pins.create_pin("board-1", "T", "D", "https://x", image_bytes=b"x", content_type="image/webp")
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
