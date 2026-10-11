import base64
import io
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient
from PIL import Image

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
RETRYABLE_ERROR = requests.HTTPError("Pinterest HTTP 400; Sorry! This site doesn't allow you to save Pins.")


class PinScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"PIN_API_KEY": "test-action-key", "PIN_WORKER_ENABLED": "0"})
        cls.environment.start()

        from api import app
        from pinterest import pin_queue, pin_worker
        from routers import schedule

        cls.client = TestClient(app)
        cls.headers = {"X-API-Key": "test-action-key"}
        cls.pin_queue = pin_queue
        cls.pin_worker = pin_worker
        cls.schedule = schedule

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.data_env = patch.dict(
            os.environ,
            {
                "PIN_DATA_DIR": self.data_dir,
                # 09:00 / 14:00 / 20:00 New York = 13:00Z / 18:00Z / 00:00Z next day while on EDT
                "PIN_DAILY_SLOTS": "09:00,14:00,20:00",
                "PIN_TIMEZONE": "America/New_York",
            },
        )
        self.data_env.start()
        self.account_boards = [{"id": "board-1", "name": "90s Nostalgia"}, {"id": "board-7", "name": "Horror Tees"}]
        self.list_boards = MagicMock(side_effect=lambda: list(self.account_boards))
        self.create_board = MagicMock(side_effect=lambda name: {"id": f"new-{name}", "name": name})
        self.board_patches = [
            patch.object(self.schedule, "list_boards", self.list_boards),
            patch.object(self.schedule, "create_board", self.create_board),
        ]
        for board_patch in self.board_patches:
            board_patch.start()

    def tearDown(self):
        for board_patch in self.board_patches:
            board_patch.stop()
        self.data_env.stop()
        shutil.rmtree(self.data_dir)

    def _schedule(self, variant_count=2, **overrides):
        body = {
            "board_name": "90s Nostalgia",
            "link": "https://www.etsy.com/listing/123/tee",
            "start_at": "2026-10-10T12:00:00Z",
            "variants": [
                {"title": f"Pin {n}", "description": f"Description {n}", "image_base64": PNG_B64}
                for n in range(1, variant_count + 1)
            ],
        }
        body.update(overrides)
        return self.client.post("/pins/schedule", headers=self.headers, json=body)

    # ---------- scheduling ----------

    def test_exact_times_space_variants_three_days_apart_and_save_images(self):
        response = self._schedule(variant_count=4, use_slots=False)

        self.assertEqual(response.status_code, 200)
        pins = response.json()["pins"]
        self.assertEqual(
            [pin["publish_at"] for pin in pins],
            ["2026-10-10T12:00:00Z", "2026-10-13T12:00:00Z", "2026-10-16T12:00:00Z", "2026-10-19T12:00:00Z"],
        )
        self.assertTrue(all(pin["status"] == "queued" and pin["board_id"] == "board-1" for pin in pins))
        self.assertEqual(len({pin["batch_id"] for pin in pins}), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.data_dir, "pin_images"))), 4)

    # ---------- slots ----------

    def _publish_times(self, response):
        return [pin["publish_at"] for pin in response.json()["pins"]]

    def test_slots_snap_variants_to_daily_slots_keeping_spacing(self):
        # start is 08:00 New York, so the first variant takes that day's 09:00 slot
        response = self._schedule(variant_count=4)

        self.assertEqual(
            self._publish_times(response),
            ["2026-10-10T13:00:00Z", "2026-10-13T13:00:00Z", "2026-10-16T13:00:00Z", "2026-10-19T13:00:00Z"],
        )

    def test_products_scheduled_together_fill_the_next_free_slots(self):
        first = self._publish_times(self._schedule(variant_count=2))
        second = self._publish_times(self._schedule(variant_count=2))
        third = self._publish_times(self._schedule(variant_count=1))
        fourth = self._publish_times(self._schedule(variant_count=1))

        self.assertEqual(first, ["2026-10-10T13:00:00Z", "2026-10-13T13:00:00Z"])
        self.assertEqual(second, ["2026-10-10T18:00:00Z", "2026-10-13T18:00:00Z"])
        self.assertEqual(third, ["2026-10-11T00:00:00Z"])  # 20:00 New York on the 10th
        self.assertEqual(fourth, ["2026-10-11T13:00:00Z"])  # the 10th is full, so next morning

    def test_spacing_is_measured_from_the_slot_actually_used(self):
        # fill Oct 13's slots so the second variant of a new product must move later
        self._schedule(variant_count=1, start_at="2026-10-13T12:00:00Z")
        self._schedule(variant_count=1, start_at="2026-10-13T12:00:00Z")
        self._schedule(variant_count=1, start_at="2026-10-13T12:00:00Z")

        times = self._publish_times(self._schedule(variant_count=3, start_at="2026-10-10T12:00:00Z"))

        # Oct 13 is full so variant 2 lands on Oct 14, and variant 3 is 3 days after that
        self.assertEqual(times, ["2026-10-10T13:00:00Z", "2026-10-14T13:00:00Z", "2026-10-17T13:00:00Z"])

    def test_cancelled_pin_frees_its_slot(self):
        pin = self._schedule(variant_count=1).json()["pins"][0]
        self.client.delete(f"/pins/scheduled/{pin['id']}", headers=self.headers)

        self.assertEqual(self._publish_times(self._schedule(variant_count=1)), [pin["publish_at"]])

    def test_slots_follow_local_time_across_daylight_saving_change(self):
        # US clocks fall back on 2026-11-01, so 09:00 New York moves from 13:00Z to 14:00Z
        times = self._publish_times(self._schedule(variant_count=2, start_at="2026-10-31T12:00:00Z", spacing_days=2))

        self.assertEqual(times, ["2026-10-31T13:00:00Z", "2026-11-02T14:00:00Z"])

    def test_schedule_downloads_file_refs_at_submit_time(self):
        download = MagicMock(return_value=PNG_BYTES)
        with patch.object(self.schedule, "_download", download):
            response = self._schedule(
                variants=[
                    {
                        "title": "Pin",
                        "description": "Desc",
                        "openaiFileIdRefs": [{"download_link": "https://files.example.com/pin.png"}],
                    }
                ]
            )

        self.assertEqual(response.status_code, 200)
        download.assert_called_once_with("https://files.example.com/pin.png")
        row = self.pin_queue.get_pin(response.json()["pins"][0]["id"])
        self.assertEqual(row["content_type"], "image/png")
        with open(row["image_path"], "rb") as f:
            self.assertEqual(f.read(), PNG_BYTES)

    def test_top_level_file_refs_map_to_variants_by_position(self):
        downloads = {
            "https://files.example.com/1.png": PNG_BYTES,
            "https://files.example.com/2.jpg": b"\xff\xd8\xff" + b"\x00" * 32,
        }
        with patch.object(self.schedule, "_download", side_effect=downloads.__getitem__):
            response = self._schedule(
                openaiFileIdRefs=[{"download_link": url} for url in downloads],
                variants=[
                    {"title": "Pin 1", "description": "Desc"},
                    {"title": "Pin 2", "description": "Desc"},
                    {"title": "Pin 3", "description": "Desc", "image_base64": PNG_B64},  # own image wins
                ],
            )

        self.assertEqual(response.status_code, 200)
        types = [self.pin_queue.get_pin(pin["id"])["content_type"] for pin in response.json()["pins"]]
        self.assertEqual(types, ["image/png", "image/jpeg", "image/png"])

    def test_variant_without_any_image_is_rejected(self):
        with patch.object(self.schedule, "_download", return_value=PNG_BYTES):
            response = self._schedule(
                openaiFileIdRefs=[{"download_link": "https://files.example.com/1.png"}],
                variants=[{"title": "Pin 1", "description": "Desc"}, {"title": "Pin 2", "description": "Desc"}],
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Variant 2: Provide openaiFileIdRefs", response.json()["detail"])
        self.assertEqual(self.pin_queue.list_pins(), [])

    def test_webp_images_are_converted_to_png_for_pinterest(self):
        webp = io.BytesIO()
        Image.new("RGB", (4, 6), "purple").save(webp, format="WEBP")
        response = self._schedule(
            variants=[{"title": "Pin", "description": "Desc", "image_base64": base64.b64encode(webp.getvalue()).decode()}]
        )

        self.assertEqual(response.status_code, 200)
        row = self.pin_queue.get_pin(response.json()["pins"][0]["id"])
        self.assertEqual(row["content_type"], "image/png")
        with open(row["image_path"], "rb") as f:
            self.assertTrue(f.read().startswith(b"\x89PNG"))

    def test_schedule_rejects_whole_request_when_one_image_is_bad(self):
        response = self._schedule(
            variants=[
                {"title": "Good", "description": "Desc", "image_base64": PNG_B64},
                {"title": "Bad", "description": "Desc", "image_base64": base64.b64encode(b"not an image").decode()},
            ]
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Variant 2", response.json()["detail"])
        self.assertEqual(self.pin_queue.list_pins(), [])

    def test_schedule_requires_a_board_and_at_most_four_variants(self):
        self.assertEqual(self._schedule(board_name=None).status_code, 400)
        self.assertEqual(self._schedule(variant_count=5).status_code, 422)

    def test_each_variant_can_target_its_own_board_and_section(self):
        sections = MagicMock(side_effect=lambda board_id, name: ({"id": f"{board_id}/{name}"}, False))
        variant = {"description": "Desc", "image_base64": PNG_B64}

        with patch.object(self.schedule, "ensure_section", sections):
            response = self._schedule(
                board_name="horror tees",  # names match case-insensitively
                section_name="Skulls",
                variants=[
                    {**variant, "title": "Default board and section"},
                    {**variant, "title": "Own section on default board", "section_name": "Reapers"},
                    {**variant, "title": "Own board", "board_name": "90s Nostalgia"},
                    {**variant, "title": "Own board and section", "board_id": "board-1", "section_name": "Grunge"},
                ],
            )

        self.assertEqual(response.status_code, 200)
        destinations = [(pin["board_id"], pin["section_id"]) for pin in response.json()["pins"]]
        self.assertEqual(
            destinations,
            [
                ("board-7", "board-7/Skulls"),
                ("board-7", "board-7/Reapers"),
                ("board-1", None),  # the request's section belongs to the request's board
                ("board-1", "board-1/Grunge"),
            ],
        )
        # the account's boards are fetched once per request, and nothing new is created
        self.assertEqual(self.list_boards.call_count, 1)
        self.create_board.assert_not_called()

    def test_unknown_board_name_creates_the_board_once(self):
        variant = {"description": "Desc", "image_base64": PNG_B64, "board_name": "Halloween Gifts"}
        response = self._schedule(variants=[{**variant, "title": "A"}, {**variant, "title": "B"}])

        self.assertEqual(response.status_code, 200)
        self.assertEqual({pin["board_id"] for pin in response.json()["pins"]}, {"new-Halloween Gifts"})
        self.create_board.assert_called_once_with("Halloween Gifts")

    def test_unknown_board_id_is_rejected_before_queuing(self):
        response = self._schedule(
            board_name=None,
            variants=[{"title": "Pin", "description": "Desc", "image_base64": PNG_B64, "board_id": "905786612495800999"}],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("isn't one of this account's boards", response.json()["detail"])
        self.assertEqual(self.pin_queue.list_pins(), [])

    def test_board_may_be_set_only_on_variants(self):
        response = self._schedule(
            board_name=None,
            variants=[{"title": "Pin", "description": "Desc", "image_base64": PNG_B64, "board_id": "board-7"}],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pins"][0]["board_id"], "board-7")

    def test_variant_without_any_board_is_rejected(self):
        response = self._schedule(
            board_name=None,
            variants=[
                {"title": "Pin 1", "description": "Desc", "image_base64": PNG_B64, "board_id": "board-7"},
                {"title": "Pin 2", "description": "Desc", "image_base64": PNG_B64},
            ],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Variant 2", response.json()["detail"])

    def test_copy_over_pinterest_limits_is_rejected_before_queuing(self):
        variant = {"title": "Pin", "description": "Desc", "image_base64": PNG_B64}
        for field, length in [("title", 101), ("description", 501), ("alt_text", 501)]:
            with self.subTest(field=field):
                self.assertEqual(self._schedule(variants=[{**variant, field: "x" * length}]).status_code, 422)

        self.assertEqual(self._schedule(variants=[{**variant, "title": "x" * 100}]).status_code, 200)
        self.assertEqual(len(self.pin_queue.list_pins()), 1)

    def test_variant_link_overrides_request_link(self):
        response = self._schedule(
            variants=[{"title": "Pin", "description": "Desc", "image_base64": PNG_B64, "link": "https://shop.example.com/p"}]
        )

        self.assertEqual(response.json()["pins"][0]["link"], "https://shop.example.com/p")

    # ---------- worker ----------

    def test_worker_posts_only_due_pins(self):
        self._schedule(variant_count=2)
        create_pin = MagicMock(return_value={"id": "pin-123"})

        with patch.object(self.pin_worker, "create_pin", create_pin):
            counts = self.pin_worker.process_due_pins(now="2026-10-11T00:00:00Z", sleep=lambda _: None)

        self.assertEqual(counts, {"posted": 1, "retrying": 0, "failed": 0})
        self.assertEqual(create_pin.call_args.kwargs["image_bytes"], PNG_BYTES)
        self.assertEqual(create_pin.call_args.kwargs["board_id"], "board-1")
        first, second = self.pin_queue.list_pins()
        self.assertEqual((first["status"], first["pin_id"]), ("posted", "pin-123"))
        self.assertEqual(second["status"], "queued")

    def test_worker_retries_save_pins_error_then_fails_after_max_attempts(self):
        self._schedule(variant_count=1)
        create_pin = MagicMock(side_effect=RETRYABLE_ERROR)
        far_future = "2099-01-01T00:00:00Z"  # due on every run despite the retry delay

        with patch.object(self.pin_worker, "create_pin", create_pin):
            outcomes = [self.pin_worker.process_due_pins(now=far_future, sleep=lambda _: None) for _ in range(3)]

        self.assertEqual([o["retrying"] for o in outcomes], [1, 1, 0])
        self.assertEqual(outcomes[2]["failed"], 1)
        row = self.pin_queue.list_pins()[0]
        self.assertEqual((row["status"], row["attempts"]), ("failed", 3))
        self.assertIn("allow you to save Pins", row["last_error"])

    def test_worker_retry_waits_before_next_attempt(self):
        self._schedule(variant_count=1)
        with patch.object(self.pin_worker, "create_pin", MagicMock(side_effect=RETRYABLE_ERROR)):
            self.pin_worker.process_due_pins(now="2026-10-11T00:00:00Z", sleep=lambda _: None)

        row = self.pin_queue.list_pins()[0]
        self.assertEqual(row["status"], "queued")
        self.assertGreater(row["next_attempt_at"], self.pin_queue.now_iso())

    def test_worker_succeeds_on_retry_after_save_pins_error(self):
        self._schedule(variant_count=1)
        create_pin = MagicMock(side_effect=[RETRYABLE_ERROR, {"id": "pin-9"}])

        with patch.object(self.pin_worker, "create_pin", create_pin):
            self.pin_worker.process_due_pins(now="2099-01-01T00:00:00Z", sleep=lambda _: None)
            self.pin_worker.process_due_pins(now="2099-01-01T00:00:00Z", sleep=lambda _: None)

        row = self.pin_queue.list_pins()[0]
        self.assertEqual((row["status"], row["pin_id"], row["attempts"]), ("posted", "pin-9", 2))
        self.assertIsNone(row["last_error"])

    def test_worker_does_not_retry_other_errors(self):
        self._schedule(variant_count=1)
        error = requests.HTTPError("Pinterest HTTP 400; code=2; Board not found")

        with patch.object(self.pin_worker, "create_pin", MagicMock(side_effect=error)):
            counts = self.pin_worker.process_due_pins(now="2099-01-01T00:00:00Z", sleep=lambda _: None)

        self.assertEqual(counts["failed"], 1)
        self.assertEqual(self.pin_queue.list_pins()[0]["status"], "failed")

    def test_interrupted_posts_are_requeued(self):
        pin_id = self._schedule(variant_count=1).json()["pins"][0]["id"]
        self.pin_queue.update_pin(pin_id, status="posting")

        self.pin_queue.requeue_interrupted()

        self.assertEqual(self.pin_queue.get_pin(pin_id)["status"], "queued")

    # ---------- status and cancel ----------

    def test_list_filters_by_status_and_cancel_only_queued(self):
        first, second = self._schedule(variant_count=2).json()["pins"]
        self.pin_queue.update_pin(first["id"], status="posted", pin_id="pin-1")

        listed = self.client.get("/pins/scheduled", headers=self.headers, params={"status": "posted"}).json()["pins"]
        self.assertEqual([(p["id"], p["pin_url"]) for p in listed], [(first["id"], "https://www.pinterest.com/pin/pin-1/")])

        self.assertEqual(self.client.delete(f"/pins/scheduled/{first['id']}", headers=self.headers).status_code, 409)
        cancelled = self.client.delete(f"/pins/scheduled/{second['id']}", headers=self.headers)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.client.delete("/pins/scheduled/9999", headers=self.headers).status_code, 404)

    def test_schedule_routes_require_api_key(self):
        self.assertEqual(self.client.get("/pins/scheduled").status_code, 401)


if __name__ == "__main__":
    unittest.main()
