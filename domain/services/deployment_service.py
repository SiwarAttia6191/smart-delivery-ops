"""Deployment-level state resolution (m4, #76).

The incarnation token: generated once per ops-database lifetime, cached
per process, stamped onto every outgoing wire message by the relay. The
raw layer keys dedupe on (incarnation, event_id) — a rebuilt ops database
gets a fresh token and can no longer collide with an older incarnation's
ids.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.models import INCARNATION_KEY, DeploymentState

_incarnation: str | None = None


def incarnation_for(session: Session) -> str:
    """The deployment's incarnation token, created on first use and cached
    for the process lifetime (it is immutable per ops-database incarnation)."""
    global _incarnation
    if _incarnation is not None:
        return _incarnation
    value = session.scalar(
        select(DeploymentState.value).where(DeploymentState.key == INCARNATION_KEY)
    )
    if value is None:
        value = uuid.uuid4().hex
        session.add(DeploymentState(key=INCARNATION_KEY, value=value))
        session.flush()
    _incarnation = value
    return value


def reset_cache() -> None:
    """Test hook: forget the process-cached token."""
    global _incarnation
    _incarnation = None
