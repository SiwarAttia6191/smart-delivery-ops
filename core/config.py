"""Application settings, loaded from environment variables or a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ────────────────────────────────────────────
    app_name: str = "Smart Delivery Ops"
    app_version: str = "0.1.0"
    environment: str = "local"

    # ── Database ───────────────────────────────────────────────
    # Production stack: PostgreSQL (matches docker-compose.yml credentials).
    # Zero-dependency fallback for quick experiments: DATABASE_URL=sqlite:///./local.db
    database_url: str = "postgresql+psycopg://smartops:smartops@localhost:5434/smart_delivery_ops"

    # ── Pricing engine (benchmarked: delivery fees $0.49–$9.99, typical $3–6;
    #    surge caps 1.2×–2.5× on modern platforms) ──────────────
    pricing_base_fee: float = 2.00
    pricing_per_km: float = 0.90
    pricing_min_fee: float = 3.00
    pricing_surge_tier1_ratio: float = 1.20
    pricing_surge_tier1_multiplier: float = 1.25
    pricing_surge_tier2_ratio: float = 1.50
    pricing_surge_tier2_multiplier: float = 1.50
    # Cap tier — modern platforms hard-cap surge; 2.0× sits inside the
    # observed 1.2×–2.5× band.
    pricing_surge_tier3_ratio: float = 2.00
    pricing_surge_tier3_multiplier: float = 2.00

    # ── ETA serving (#26/#69): empty = heuristic champion; "registry" loads
    #    models:/eta_model/Production at startup, heuristic fallback on any
    #    failure (ML being down must never break quoting).
    eta_model_source: str = ""
    eta_model_name: str = "eta_model"
    eta_model_stage: str = "Production"
    mlflow_tracking_uri: str = "http://localhost:5100"

    # ── ETA heuristic (Phase 1; replaced by ML model in Phase 3)
    # 20 km/h ≈ urban courier travel speed (17–21 km/h benchmark);
    # a 6 km trip lands near 35 min total, matching observed city averages.
    eta_avg_speed_kmh: float = 20.0
    eta_restaurant_prep_minutes: int = 12
    eta_buffer_minutes: int = 5

    # ── Geo providers (Phase 3) ────────────────────────────────
    # Champion (business decisions) vs optional shadow challenger (logged
    # alongside, never used, until the benchmark evidence says otherwise).
    # Providers: haversine | circuity | osrm.
    distance_provider: str = "haversine"
    distance_shadow_provider: str | None = None
    distance_circuity_factor: float = 1.3
    osrm_base_url: str = "http://localhost:5000"

    # ── Event pipeline (Phase 2) ───────────────────────────────
    # Host default points at the compose host mapping (localhost:9094);
    # containers override it with kafka:19092.
    kafka_bootstrap_servers: str = "localhost:9094"
    kafka_orders_topic: str = "orders.events"
    kafka_drivers_topic: str = "drivers.events"
    # Outbox relay tuning: batch drain, idle poll, and the cap the loop
    # backs off to when the broker or database is unreachable.
    relay_batch_size: int = 100
    relay_poll_seconds: float = 2.0
    relay_backoff_seconds: float = 60.0
    # Warehouse ingestion (Phase 2): where the consumer lands events and how.
    warehouse_database_url: str = (
        "postgresql+psycopg://smartops:smartops@localhost:5434/smart_delivery_warehouse"
    )
    kafka_group_id: str = "warehouse-ingest"
    ingest_batch_size: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
