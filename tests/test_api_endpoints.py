import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"PIN_API_KEY": "test-action-key"})
        cls.environment.start()

        from api import app

        cls.client = TestClient(app)
        cls.headers = {"X-API-Key": "test-action-key"}

        from routers import boards, pins, research

        cls.boards = boards
        cls.pins = pins
        cls.research = research

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def test_openapi_exposes_all_action_operations_and_optional_image_ref(self):
        schema = self.client.get("/openapi.json").json()
        operations = {
            operation["operationId"]
            for path in schema["paths"].values()
            for operation in path.values()
            if isinstance(operation, dict) and "operationId" in operation
        }

        self.assertTrue(
            {
                "getBoards",
                "getCategories",
                "ensureBoard",
                "ensureSection",
                "createPin",
                "createPinByBoard",
                "createPinsBatch",
                "createPinsBatchByBoard",
                "generateIdeas",
                "getTrendingNow",
                "getPinterestTrends",
                "getAmazonBestsellers",
                "getEtsyTrending",
            }.issubset(operations)
        )
        pin_schema = schema["components"]["schemas"]["PinRequest"]
        self.assertNotIn("openaiFileIdRefs", pin_schema.get("required", []))
        platforms_schema = schema["components"]["schemas"]["GenerateIdeasRequest"]["properties"]["platforms"]
        self.assertEqual(platforms_schema["type"], "array")
        self.assertEqual(
            platforms_schema["items"]["enum"],
            ["amazon", "youtube", "etsy", "pinterest", "tiktok", "google_trends"],
        )

    def test_get_boards_returns_pinterest_boards(self):
        with patch.object(self.boards, "list_boards", return_value=[{"id": "board-1", "name": "Ideas"}]):
            response = self.client.get("/boards", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"boards": [{"id": "board-1", "name": "Ideas"}]})

    def test_get_boards_reports_upstream_failure_reason(self):
        with patch.object(self.boards, "list_boards", side_effect=RuntimeError("stored Pinterest token is invalid")):
            response = self.client.get("/boards", headers=self.headers)

        self.assertEqual(response.status_code, 502)
        self.assertIn("stored Pinterest token is invalid", response.json()["detail"])

    def test_categories_remain_available_for_category_pin_route(self):
        response = self.client.get("/categories", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertIn("categories", response.json())

    def test_pin_accepts_public_image_url_without_file_reference(self):
        with patch.object(self.pins, "create_pin_for_listing", return_value={"id": "pin-1"}):
            response = self.client.post(
                "/create-pin",
                headers=self.headers,
                json={
                    "product_title": "Test product",
                    "category": "90s-nostalgia",
                    "link": "https://shop.example/product",
                    "image_url": "https://cdn.example/pin.png",
                    "title": "Test pin",
                    "description": "A test pin",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pin_id"], "pin-1")

    def test_batch_returns_item_failure_for_unusable_image_url(self):
        response = self.client.post(
            "/create-pins-batch",
            headers=self.headers,
            json={
                "pins": [
                    {
                        "product_title": "Test product",
                        "category": "90s-nostalgia",
                        "link": "https://shop.example/product",
                        "image_url": "sandbox:/mnt/data/pin.png",
                        "title": "Test pin",
                        "description": "A test pin",
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "failed")

    def test_pinterest_trends_reports_missing_scope_as_forbidden(self):
        with patch.object(
            self.research,
            "get_pinterest_trends",
            side_effect=self.research.PinterestTrendsError(401, "scope unavailable"),
        ):
            response = self.client.get("/pinterest-trends", headers=self.headers)

        self.assertEqual(response.status_code, 403)
        self.assertIn("trends:read", response.json()["detail"])

    def test_pinterest_trends_returns_growth_metrics(self):
        with patch.object(
            self.research,
            "get_pinterest_trends",
            return_value=[
                {
                    "keyword": "summer nails",
                    "pct_growth_wow": 30,
                    "pct_growth_mom": 100,
                    "pct_growth_yoy": 10,
                    "time_series": {"2026-10-05": 73},
                }
            ],
        ) as get_trends:
            response = self.client.get(
                "/pinterest-trends?region=us&trend_type=growing&limit=1",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "US")
        self.assertEqual(response.json()["trends"][0]["pct_growth_wow"], 30)
        get_trends.assert_called_once_with(region="US", trend_type="growing", limit=1)

    def test_ensure_board_can_create_a_section(self):
        with patch.object(self.boards, "ensure_board", return_value=({"id": "board-1", "name": "Ideas"}, True)), patch.object(
            self.boards, "ensure_section", return_value=({"id": "section-1"}, True)
        ):
            response = self.client.post(
                "/boards/ensure",
                headers=self.headers,
                json={"name": "Ideas", "section_name": "Funny mugs"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["section_id"], "section-1")

    def test_ensure_section_can_resolve_board_name(self):
        with patch.object(self.boards, "ensure_board", return_value=({"id": "board-1", "name": "Ideas"}, False)), patch.object(
            self.boards, "ensure_section", return_value=({"id": "section-1"}, False)
        ):
            response = self.client.post(
                "/board-sections/ensure",
                headers=self.headers,
                json={"board_name": "Ideas", "section_name": "Funny mugs"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["board_name"], "Ideas")

    def test_create_pin_by_board_returns_resolved_ids(self):
        with patch.object(self.pins, "ensure_board", return_value=({"id": "board-1"}, False)), patch.object(
            self.pins, "create_pin", return_value={"id": "pin-1"}
        ):
            response = self.client.post(
                "/create-pin-by-board",
                headers=self.headers,
                json={
                    "board_name": "Ideas",
                    "title": "Test pin",
                    "description": "A test pin",
                    "link": "https://shop.example/product",
                    "image_url": "https://cdn.example/image.png",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["board_id"], "board-1")
        self.assertEqual(response.json()["pin_id"], "pin-1")

    def test_amazon_bestsellers_rejects_unknown_category(self):
        response = self.client.get("/amazon-bestsellers?category=unknown", headers=self.headers)

        self.assertEqual(response.status_code, 400)

    def test_etsy_endpoint_returns_structured_listing_signals(self):
        listing = {
            "listingId": 123,
            "title": "Funny mug",
            "url": "https://etsy.example/listing/123",
            "price": 15.0,
            "inCartCount": 2,
            "isBestseller": True,
            "isStarSeller": False,
            "lastSaleDate": None,
        }
        with patch.object(self.research, "APIFY_API_TOKEN", "test-token"), patch.object(
            self.research, "etsy_trending_listings", return_value=[listing]
        ):
            response = self.client.get("/etsy-trending?seed=funny%20mug", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        result = response.json()["listings"][0]
        self.assertEqual(result["listing_id"], 123)
        self.assertEqual(result["trend_score"], 5)

    def test_etsy_endpoint_reports_missing_apify_key(self):
        with patch.object(self.research, "APIFY_API_TOKEN", ""):
            response = self.client.get("/etsy-trending?seed=funny%20mug", headers=self.headers)

        self.assertEqual(response.status_code, 503)

    def test_deep_idea_request_rejects_excessive_platforms(self):
        response = self.client.post(
            "/generate-ideas",
            headers=self.headers,
            json={"seeds": ["funny mug"], "platforms": ["amazon", "youtube", "etsy"], "deep": True},
        )

        self.assertEqual(response.status_code, 400)

    def test_generate_ideas_returns_requested_seed_groups(self):
        with patch.object(self.research, "collect_ideas", return_value={}), patch.object(
            self.research, "group_by_seed", return_value={}
        ):
            response = self.client.post(
                "/generate-ideas",
                headers=self.headers,
                json={"seeds": ["funny mug"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"groups": [{"seed": "funny mug", "ideas": []}]})

    def test_google_trending_route_normalizes_region(self):
        with patch.object(self.research, "google_trending_now", return_value=[("test trend", "5000+")]):
            response = self.client.get("/trending-now?geo=us", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["geo"], "US")
        self.assertEqual(response.json()["items"][0]["title"], "test trend")

    def test_amazon_bestsellers_returns_titles(self):
        with patch.object(self.research, "amazon_bestsellers", return_value=["Funny mug", "Novelty mug"]):
            response = self.client.get("/amazon-bestsellers?category=home-kitchen&limit=1", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["titles"], ["Funny mug"])

    def test_batch_by_board_publishes_each_valid_pin(self):
        with patch.object(self.pins, "ensure_board", return_value=({"id": "board-1"}, False)), patch.object(
            self.pins, "create_pin", return_value={"id": "pin-1"}
        ):
            response = self.client.post(
                "/create-pins-batch-by-board",
                headers=self.headers,
                json={
                    "pins": [
                        {
                            "board_name": "Ideas",
                            "title": "Test pin",
                            "description": "A test pin",
                            "link": "https://shop.example/product",
                            "image_url": "https://cdn.example/image.png",
                        }
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()