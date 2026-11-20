"""Customer schemas."""

from datetime import datetime

from pydantic import Field

from apps.api.schemas.common import LatLonMixin, ORMModel


class CustomerCreate(LatLonMixin):
    name: str = Field(..., min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=30)


class CustomerResponse(ORMModel):
    id: int
    name: str
    phone: str | None
    latitude: float
    longitude: float
    loyalty_points: int = 0
    created_at: datetime


class CustomerPublicResponse(ORMModel):
    """The anonymous-GET shape (#192): no phone — the one clear PII field.
    Name stays: it is the storefront's visible identity, not contact data."""

    id: int
    name: str
    latitude: float
    longitude: float


class CustomerOtpRequest(ORMModel):
    """The phone the customer gave at checkout — the identity key (#309)."""

    phone: str


class CustomerOtpIssued(ORMModel):
    """The challenge is minted. V1 demo posture: the code rides the
    response (`dev_otp`) because there is no SMS provider — a real
    deployment swaps the send, not the contract."""

    sent: bool
    expires_in: int
    dev_otp: str


class CustomerOtpVerifyRequest(ORMModel):
    phone: str
    code: str


class CustomerOtpVerified(ORMModel):
    access_token: str
    token_type: str = "bearer"
    customer: CustomerResponse
