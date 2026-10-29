"""Integration: the /config endpoint serves the deployment's city manifest (#78)."""

from fastapi.testclient import TestClient


class TestCityConfig:
    def test_config_serves_default_city(self, client: TestClient):
        body = client.get("/config")
        assert body.status_code == 200
        config = body.json()
        assert config["city"] == "tunis"
        assert config["label"] == "Tunis"
        assert config["business_timezone"] == "Africa/Tunis"
        assert config["map"]["center"][0] == 36.8065
        assert config["map"]["zoom"] == 12

    def test_config_appears_in_openapi(self, client: TestClient):
        paths = client.get("/openapi.json").json()["paths"]
        assert "/config" in paths
