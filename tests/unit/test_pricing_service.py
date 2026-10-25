"""Unit tests for the pricing rules and surge tiers."""

import pytest

from core.config import Settings
from domain.rules import pricing_rules


@pytest.fixture
def settings() -> Settings:
    return Settings(
        pricing_base_fee=2.0,
        pricing_per_km=0.9,
        pricing_min_fee=3.0,
        pricing_surge_tier1_ratio=1.2,
        pricing_surge_tier1_multiplier=1.25,
        pricing_surge_tier2_ratio=1.5,
        pricing_surge_tier2_multiplier=1.5,
        pricing_surge_tier3_ratio=2.0,
        pricing_surge_tier3_multiplier=2.0,
    )


class TestSurgeMultiplier:
    def test_no_surge_below_tier1(self, settings):
        assert pricing_rules.surge_multiplier(0.5, settings) == 1.0
        assert pricing_rules.surge_multiplier(1.19, settings) == 1.0

    def test_tier1_at_threshold(self, settings):
        assert pricing_rules.surge_multiplier(1.2, settings) == 1.25
        assert pricing_rules.surge_multiplier(1.49, settings) == 1.25

    def test_tier2_at_threshold(self, settings):
        assert pricing_rules.surge_multiplier(1.5, settings) == 1.5
        assert pricing_rules.surge_multiplier(1.99, settings) == 1.5

    def test_tier3_caps_extreme_demand(self, settings):
        assert pricing_rules.surge_multiplier(2.0, settings) == 2.0
        assert pricing_rules.surge_multiplier(5.0, settings) == 2.0  # hard cap


class TestComputePrice:
    def test_base_plus_distance(self, settings):
        price = pricing_rules.compute_price(10.0, 1.0, settings)
        assert price.base_fee == 2.0
        assert price.distance_fee == 9.0
        assert price.surge_multiplier == 1.0
        assert price.total == 11.0  # max(3.0, (2.0 + 9.0) * 1.0)

    def test_min_fee_floor(self, settings):
        price = pricing_rules.compute_price(0.5, 1.0, settings)
        subtotal = 2.0 + round(0.5 * 0.9, 2)  # 2.45 < 3.0 min
        assert subtotal < settings.pricing_min_fee
        assert price.total == settings.pricing_min_fee

    def test_surge_applies_to_subtotal(self, settings):
        price = pricing_rules.compute_price(10.0, 1.3, settings)  # tier 1
        assert price.surge_multiplier == 1.25
        assert price.total == round((2.0 + 9.0) * 1.25, 2)

    def test_negative_distance_rejected(self, settings):
        with pytest.raises(ValueError):
            pricing_rules.compute_price(-1.0, settings=settings)

    def test_negative_demand_rejected(self, settings):
        with pytest.raises(ValueError):
            pricing_rules.compute_price(5.0, demand_ratio=-0.1, settings=settings)
