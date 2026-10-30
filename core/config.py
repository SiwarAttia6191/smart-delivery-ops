"""Application settings, loaded from environment variables or a local .env file."""

import urllib.parse
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _url_with_password(url: str, password: str) -> str:
    """Replace the password component of a SQLAlchemy URL (secret-file support)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    userinfo = parts.username or ""
    if password:
        userinfo = f"{userinfo}:{urllib.parse.quote(password, safe='')}"
    netloc = f"{userinfo}@{host}{port}" if userinfo else f"{host}{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


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

    # ── Business timezone (#78): IANA zone name. Storage stays UTC; local
    # business grains (dbt staging, ML calendar features, forecast row keys)
    # derive through the tz database — never a fixed offset (DST-safe).
    business_timezone: str = "Africa/Tunis"

    # ── Region pack (#78): which city manifest (config/cities/<city>.json)
    # drives seeds, the demo map and /config. One deployment = one city;
    # swapping region = fresh databases + the matching BUSINESS_TIMEZONE
    # (see README "Run it in another city").
    city: str = "tunis"

    # ── Auth (#28): JWTs guard every mutating endpoint. The demo posture
    # keeps GETs open (aggregates + demo surfaces) — a production profile
    # closes reads per role. The default secret is DEV-ONLY: real
    # deployments mount AUTH_JWT_SECRET_FILE (a Docker secret) or set
    # AUTH_JWT_SECRET outside local development.
    auth_jwt_secret: str = "dev-only-change-me"
    auth_token_expire_minutes: int = 720  # 12h — a working demo day

    # ── Secrets (#28 secrets half): credential files mounted as Docker
    # secrets. When set, the file's contents OVERRIDE the password in both
    # database URLs / the JWT secret — the inline values in the URLs and
    # the compose defaults become dev fallbacks, not the source of truth.
    # Local dev generates them with scripts/ensure_secrets.py; production
    # mounts vault-managed files — no code change.
    db_password_file: str | None = None
    jwt_secret_file: str | None = None

    # ── ETA serving (#26/#69): empty = heuristic champion; "registry" loads
    #    models:/eta_model/Production at startup, heuristic fallback on any
    #    failure (ML being down must never break quoting).
    eta_model_source: str = ""
    eta_model_name: str = "eta_model"
    eta_model_stage: str = "Production"
    # Hot reload (m-series, #76): how often the serving path may re-check
    # the registry's promoted version and swap the model without a restart.
    eta_model_reload_ttl_seconds: int = 60
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

    # ── Driver location updates (#60): a location_update event is emitted
    # only when the driver moved further than this from the last evented
    # position (hysteresis - volume scales with movement, not with pings).
    # Heartbeats go silent (is_available flips false) after the stale window:
    # the worker sweep closes their supply interval so dead drivers stop
    # counting in the supply mart.
    driver_location_update_threshold_m: float = 500.0
    # m5 (#76): sub-threshold drift that baseline resets (availability
    # churn) keep masking still trips a location_update past this
    # cumulative distance from the driver's last real location_update.
    driver_drift_emission_threshold_m: float = 2000.0
    driver_stale_after_minutes: int = 120

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

    @model_validator(mode="after")
    def _absorb_secret_files(self) -> "Settings":
        """Docker-secret files override inline credentials (see secrets block)."""
        if self.db_password_file:
            password = Path(self.db_password_file).read_text(encoding="utf-8").strip()
            if not password:
                raise ValueError(f"secret file {self.db_password_file} is empty")
            self.database_url = _url_with_password(self.database_url, password)
            self.warehouse_database_url = _url_with_password(self.warehouse_database_url, password)
        if self.jwt_secret_file:
            self.auth_jwt_secret = Path(self.jwt_secret_file).read_text(encoding="utf-8").strip()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
