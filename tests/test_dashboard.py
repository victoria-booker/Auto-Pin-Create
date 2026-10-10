import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PASSWORD = "test-action-key"


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"PIN_API_KEY": "test-action-key", "PIN_WORKER_ENABLED": "0"})
        cls.environment.start()

        from api import app
        from pinterest import pin_queue
        from routers import dashboard

        cls.app = app
        cls.pin_queue = pin_queue
        cls.dashboard = dashboard

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.data_env = patch.dict(os.environ, {"PIN_DATA_DIR": self.data_dir, "PIN_TIMEZONE": "America/Chicago"})
        self.data_env.start()
        self.boards_patch = patch.object(self.dashboard, "list_boards", return_value=[{"id": "board-1", "name": "Horror Tees"}])
        self.boards_patch.start()
        self.dashboard._board_names["fetched_at"] = 0
        self.client = TestClient(self.app)
        self.client.post("/dashboard/login", data={"password": PASSWORD})

    def tearDown(self):
        self.boards_patch.stop()
        self.data_env.stop()
        shutil.rmtree(self.data_dir)

    def _add_pin(self, title="Reaper Tee", publish_at="2026-10-13T14:00:00Z"):
        row = {
            "board_id": "board-1",
            "title": title,
            "description": "Desc",
            "link": "https://www.etsy.com/listing/1/reaper",
            "image_path": self.pin_queue.save_image(PNG_BYTES, "image/png"),
            "content_type": "image/png",
            "publish_at": publish_at,
        }
        return self.pin_queue.add_pins([row])[0]["id"]

    def _post(self, path, **form):
        return self.client.post(path, data=form, follow_redirects=False)

    # ---------- access ----------

    def test_signed_out_visitors_are_sent_to_login(self):
        anonymous = TestClient(self.app)
        for path in ["/dashboard", "/dashboard/images/1"]:
            response = anonymous.get(path, follow_redirects=False)
            self.assertEqual((response.status_code, response.headers["location"]), (303, "/dashboard/login"))
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_wrong_password_is_refused(self):
        anonymous = TestClient(self.app)
        with patch.object(self.dashboard, "FAILED_LOGIN_DELAY_SECONDS", 0):
            response = anonymous.post("/dashboard/login", data={"password": "wrong"})

        self.assertEqual(response.status_code, 401)
        self.assertIn("Wrong password", response.text)
        self.assertEqual(anonymous.get("/dashboard", follow_redirects=False).status_code, 303)

    def test_login_sets_long_lived_http_only_cookie(self):
        response = TestClient(self.app).post("/dashboard/login", data={"password": PASSWORD}, follow_redirects=False)

        cookie = response.headers["set-cookie"]
        self.assertEqual(response.status_code, 303)
        self.assertIn("Max-Age=7776000", cookie)  # 90 days
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)

    def test_dashboard_password_overrides_api_key_and_rotating_it_signs_out(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "chosen-password"}):
            # the session from setUp was signed with the old password
            self.assertEqual(self.client.get("/dashboard", follow_redirects=False).status_code, 303)
            with patch.object(self.dashboard, "FAILED_LOGIN_DELAY_SECONDS", 0):
                self.assertEqual(self.client.post("/dashboard/login", data={"password": PASSWORD}).status_code, 401)
            self.client.post("/dashboard/login", data={"password": "chosen-password"})
            self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_tampered_or_expired_session_is_rejected(self):
        self.assertFalse(self.dashboard._valid_session("9999999999.forged"))
        self.assertFalse(self.dashboard._valid_session("not-a-session"))
        expired = 1000
        self.assertFalse(self.dashboard._valid_session(f"{expired}.{self.dashboard._signature(expired)}"))

    def test_logout_clears_session(self):
        self.client.post("/dashboard/logout")
        self.assertEqual(self.client.get("/dashboard", follow_redirects=False).status_code, 303)

    def test_dashboard_is_hidden_from_gpt_schema(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertFalse(any(path.startswith("/dashboard") for path in paths))

    def test_rejects_cross_site_form_posts(self):
        pin_id = self._add_pin()
        response = self.client.post(
            f"/dashboard/pins/{pin_id}/cancel", headers={"Origin": "https://evil.example.com"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.pin_queue.get_pin(pin_id)["status"], "queued")

    # ---------- page ----------

    def test_page_shows_pin_with_local_time_board_name_and_thumbnail(self):
        pin_id = self._add_pin()
        page = self.client.get("/dashboard").text

        self.assertIn("Reaper Tee", page)
        self.assertIn("Tue Oct 13, 9:00 AM CDT", page)  # 14:00Z in Chicago
        self.assertIn("Horror Tees", page)
        self.assertIn(f'src="/dashboard/images/{pin_id}"', page)
        self.assertIn('value="2026-10-13T09:00"', page)
        self.assertIn("Upcoming (1)", page)

    def test_page_escapes_pin_text(self):
        self._add_pin(title="<script>alert(1)</script>")
        page = self.client.get("/dashboard").text

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_tabs_filter_by_status(self):
        self._add_pin(title="Queued pin")
        failed_id = self._add_pin(title="Failed pin")
        self.pin_queue.update_pin(failed_id, status="failed", last_error="Board not found")

        upcoming = self.client.get("/dashboard?tab=upcoming").text
        failed = self.client.get("/dashboard?tab=failed").text

        self.assertIn("Queued pin", upcoming)
        self.assertNotIn("Failed pin", upcoming)
        self.assertIn("Failed pin", failed)
        self.assertIn("Board not found", failed)

    def test_thumbnail_serves_saved_image(self):
        pin_id = self._add_pin()
        response = self.client.get(f"/dashboard/images/{pin_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG_BYTES)
        self.assertEqual(self.client.get("/dashboard/images/9999").status_code, 404)

    # ---------- actions ----------

    def test_reschedule_converts_local_time_to_utc(self):
        pin_id = self._add_pin()
        response = self._post(f"/dashboard/pins/{pin_id}/reschedule", when="2026-10-20T19:30")

        self.assertEqual(response.status_code, 303)
        row = self.pin_queue.get_pin(pin_id)
        self.assertEqual(row["publish_at"], "2026-10-21T00:30:00Z")
        self.assertEqual(row["next_attempt_at"], "2026-10-21T00:30:00Z")

    def test_post_now_makes_pin_due(self):
        pin_id = self._add_pin(publish_at="2099-01-01T00:00:00Z")
        self._post(f"/dashboard/pins/{pin_id}/post-now")

        due_ids = [row["id"] for row in self.pin_queue.due_pins()]
        self.assertIn(pin_id, due_ids)

    def test_cancel_and_requeue(self):
        pin_id = self._add_pin()
        self._post(f"/dashboard/pins/{pin_id}/cancel")
        self.assertEqual(self.pin_queue.get_pin(pin_id)["status"], "cancelled")

        self._post(f"/dashboard/pins/{pin_id}/requeue", when="2026-10-22T14:00")
        row = self.pin_queue.get_pin(pin_id)
        self.assertEqual((row["status"], row["attempts"], row["publish_at"]), ("queued", 0, "2026-10-22T19:00:00Z"))

    def test_requeue_clears_failure(self):
        pin_id = self._add_pin()
        self.pin_queue.update_pin(pin_id, status="failed", attempts=3, last_error="boom")

        self._post(f"/dashboard/pins/{pin_id}/requeue", when="2026-10-22T14:00")

        row = self.pin_queue.get_pin(pin_id)
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("queued", 0, None))

    def test_actions_refuse_wrong_state(self):
        pin_id = self._add_pin()
        self.pin_queue.update_pin(pin_id, status="posted", pin_id="p1")

        response = self._post(f"/dashboard/pins/{pin_id}/reschedule", when="2026-10-20T19:30")

        self.assertIn("Only queued pins can be rescheduled", unquote(response.headers["location"]))
        self.assertEqual(self.pin_queue.get_pin(pin_id)["status"], "posted")

    def test_invalid_time_is_rejected(self):
        pin_id = self._add_pin()
        self.assertEqual(self._post(f"/dashboard/pins/{pin_id}/reschedule", when="next tuesday").status_code, 400)


if __name__ == "__main__":
    unittest.main()
