"""Payment schemas (#159 item 2)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: str
    amount: float
    status: str
    provider: str
    provider_ref: str
    created_at: datetime
    paid_at: datetime
