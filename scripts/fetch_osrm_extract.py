"""Download a city's OSM extract into data/osrm/<extract_id>/ (#78 region pack).

Geofabrik publishes a ready-made extract for every region on Earth; the
OSRM container pre-processes it (extract → partition → customize) into a
routable graph. The download happens on the HOST — the OSRM image ships
no curl/wget. Afterwards (see README "Run it in another city"):

    OSRM_EXTRACT_ID=<id> OSRM_DATA_DIR=data/osrm/<id> \
        docker compose run --rm osrm-prepare
    OSRM_EXTRACT_ID=<id> OSRM_DATA_DIR=data/osrm/<id> \
        docker compose --profile tools up -d osrm
"""

import argparse
import sys
from pathlib import Path
from urllib.request import urlretrieve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.city_config import load_city_config  # noqa: E402


def _progress(blocks: int, block_size: int, total: int) -> None:
    if total > 0 and blocks % 2000 == 0:
        print(f"  {blocks * block_size / 1e6:.0f} / {total / 1e6:.0f} MB", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="tunis")
    parser.add_argument("--dest", default=None, help="override data/osrm/<extract_id>")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    config = load_city_config(args.region)
    dest = Path(args.dest) if args.dest else Path("data/osrm") / config.osrm.extract_id
    dest.mkdir(parents=True, exist_ok=True)
    pbf = dest / f"{config.osrm.extract_id}-latest.osm.pbf"
    if pbf.exists() and not args.force:
        print(f"{pbf} already exists - skipping (use --force to re-download).")
        return
    print(f"Downloading {config.osrm.download_url} ...")
    urlretrieve(config.osrm.download_url, pbf, reporthook=_progress)  # noqa: S310
    print(f"Saved {pbf} ({pbf.stat().st_size / 1e6:.0f} MB).")
    print(
        "Next: OSRM_EXTRACT_ID="
        f"{config.osrm.extract_id} OSRM_DATA_DIR={dest.as_posix()} "
        "docker compose run --rm osrm-prepare"
    )


if __name__ == "__main__":
    main()
