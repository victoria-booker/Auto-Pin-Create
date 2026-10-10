import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(
            os.environ,
            {
                "PIN_API_KEY": "test-action-key",
                "RENDER_EXTERNAL_URL": "https://pins.example.onrender.com/",
            },
        )
        cls.environment.start()

        from api import app

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def test_health_check_is_public(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_openapi_uses_render_url_and_custom_header_auth(self):
        schema = self.client.get("/openapi.json").json()

        self.assertEqual(schema["servers"][0]["url"], "https://pins.example.onrender.com")
        schemes = schema["components"]["securitySchemes"].values()
        self.assertTrue(any(scheme.get("name") == "X-API-Key" for scheme in schemes))

    def test_board_route_requires_action_key(self):
        self.assertEqual(self.client.get("/boards").status_code, 401)
        response = self.client.get("/boards", headers={"X-API-Key": "test-action-key"})

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()