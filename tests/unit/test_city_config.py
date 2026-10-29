"""City manifest loader + timezone guard (#78 region pack)."""

import json
from pathlib import Path

import pytest

from domain.city_config import (
    CITY_CONFIG_DIR,
    available_cities,
    ensure_business_timezone,
    load_city_config,
    load_city_manifest_json,
)


def test_loads_tunis_manifest() -> None:
    config = load_city_config("tunis")
    assert config.label == "Tunis"
    assert config.business_timezone == "Africa/Tunis"
    assert len(config.restaurants) == 6
    assert len(config.drivers) == 8
    assert config.osrm.extract_id == "tunisia"
    assert config.osrm.download_url.endswith("tunisia-latest.osm.pbf")


def test_loads_paris_manifest() -> None:
    config = load_city_config("paris")
    assert config.business_timezone == "Europe/Paris"
    assert config.osrm.extract_id == "ile-de-france"
    assert all(d.vehicle in {"motorcycle", "car", "bicycle"} for d in config.drivers)
    # Paris grid stays inside the dense central network
    assert config.benchmark_grid.spread_deg < 0.15


def test_unknown_city_lists_available() -> None:
    with pytest.raises(FileNotFoundError, match="paris"):
        load_city_config("mars")


def test_available_cities() -> None:
    assert set(available_cities()) >= {"tunis", "paris"}


def test_file_name_must_match_declared_city(tmp_path: Path) -> None:
    manifest = load_city_manifest_json("tunis")  # declares city "tunis"...
    (tmp_path / "lyon.json").write_text(json.dumps(manifest), encoding="utf-8")  # ...in lyon.json
    with pytest.raises(ValueError, match="must match"):
        load_city_config("lyon", directory=tmp_path)


def test_invalid_timezone_rejected(tmp_path: Path) -> None:
    manifest = load_city_manifest_json("tunis")
    manifest["city"] = "bad"
    manifest["business_timezone"] = "Mars/Olympus"
    (tmp_path / "bad.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="IANA"):
        load_city_config("bad", directory=tmp_path)


def test_timezone_guard_blocks_mismatch() -> None:
    config = load_city_config("paris")
    with pytest.raises(SystemExit, match="Europe/Paris"):
        ensure_business_timezone(config, "Africa/Tunis")


def test_timezone_guard_allows_match() -> None:
    config = load_city_config("paris")
    ensure_business_timezone(config, "Europe/Paris")  # no SystemExit


def test_every_manifest_is_valid() -> None:
    for name in available_cities():
        config = load_city_config(name)
        assert config.customers and config.restaurants and config.drivers
        assert 1 <= config.map.zoom <= 19


def test_manifest_seed_lists_shape() -> None:
    manifest = json.loads((CITY_CONFIG_DIR / "paris.json").read_text(encoding="utf-8"))
    assert all(r["name"] and -90 <= r["latitude"] <= 90 for r in manifest["restaurants"])
