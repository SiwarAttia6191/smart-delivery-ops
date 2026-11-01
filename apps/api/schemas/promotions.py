"""Promotions schemas (#159 item 3): coupon management."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=60)
    discount_kind: Literal["percent", "amount"]
    discount_value: float = Field(gt=0)
    expires_at: datetime | None = None
    max_redemptions: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalize(self) -> "CouponCreate":
        self.code = self.code.strip().upper()
        if self.discount_kind == "percent" and self.discount_value > 100:
            raise ValueError("percent discount cannot exceed 100")
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return self


class CouponOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    discount_kind: str
    discount_value: float
    is_active: bool
    expires_at: datetime | None
    max_redemptions: int | None
    times_used: int
    created_at: datetime
