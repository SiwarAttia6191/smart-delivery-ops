"""Validate and apply the repository's description/topics (rule 8).

The repo's GitHub metadata is a REPRESENTATION like the README: it lives
checked-in as ``repo-meta.json`` so changes are diffable and reviewable
in PRs, and this script is the only writer.

    python scripts/repo_meta.py --check    # offline: validate the caps
    python scripts/repo_meta.py --apply    # diff against GitHub, then PATCH

Why the script exists: GitHub validates server-side and SILENTLY rejects
a whole PATCH when one field violates a cap — descriptions cap at 350
characters, topics at 20 (each lowercase, hyphen-separated). The first
metadata update lost its topics to exactly that. The --check mode makes
the caps a local, pre-push failure instead of a silent remote one; the
--apply mode shows the diff before writing and uses the two-endpoint
dance (PATCH /repos for the description, PUT /repos/.../topics for the
topics — the topics field on the repo PATCH is not reliable).

Auth: the same git credential helper the repo pushes with (no token in
env or files). --check needs no network and no token.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

META_PATH = Path(__file__).resolve().parents[1] / "repo-meta.json"
API = "https://api.github.com/repos/SiwarAttia6191/smart-delivery-ops"
DESCRIPTION_MAX = 350
TOPICS_MAX = 20
TOPIC_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def load_meta() -> dict:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    return meta


def validate(meta: dict) -> list[str]:
    errors = []
    description = meta.get("description", "")
    if len(description) > DESCRIPTION_MAX:
        errors.append(
            f"description is {len(description)} chars (cap {DESCRIPTION_MAX}): "
            "trim it — GitHub rejects the WHOLE PATCH otherwise"
        )
    topics = meta.get("topics", [])
    if len(topics) > TOPICS_MAX:
        errors.append(f"{len(topics)} topics (cap {TOPICS_MAX}) — drop the least differentiating")
    for topic in topics:
        if not TOPIC_RE.match(topic):
            errors.append(f"topic '{topic}' must be lowercase alphanumerics/hyphens")
    homepage = meta.get("homepage", "")
    if homepage and not homepage.startswith("https://"):
        errors.append("homepage must be https")
    return errors


def _token() -> str:
    filled = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n",
        capture_output=True,
        text=True,
    ).stdout
    for line in filled.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("no GitHub credential found (git credential fill came up empty)")


def _api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode("utf-8") if payload else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def current() -> dict:
    repo = _api("")
    topics = _api("/topics").get("names", [])
    return {"description": repo.get("description") or "", "topics": topics}


def apply(meta: dict) -> None:
    now = current()
    if now["description"] != meta["description"]:
        print(f"description:\n- {now['description']}\n+ {meta['description']}")
    dropped = sorted(set(now["topics"]) - set(meta["topics"]))
    added = sorted(set(meta["topics"]) - set(now["topics"]))
    if dropped:
        print(f"topics - {dropped}")
    if added:
        print(f"topics + {added}")
    if now == meta:
        print("metadata already in sync — nothing to apply")
        return
    _api("", method="PATCH", payload={"description": meta["description"], "homepage": meta["homepage"]})
    _api("/topics", method="PUT", payload={"names": meta["topics"]})
    print("applied: description via PATCH /repos, topics via PUT /topics")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the caps (offline)")
    parser.add_argument("--apply", action="store_true", help="diff against GitHub, then write")
    args = parser.parse_args()
    if not args.check and not args.apply:
        parser.error("choose --check or --apply")

    meta = load_meta()
    errors = validate(meta)
    print(f"description: {len(meta['description'])}/{DESCRIPTION_MAX} chars · topics: {len(meta['topics'])}/{TOPICS_MAX}")
    if errors:
        for error in errors:
            print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("caps OK")
    if args.apply:
        apply(meta)


if __name__ == "__main__":
    main()
