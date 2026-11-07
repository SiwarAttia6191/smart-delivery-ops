"""Domain exceptions mapped to HTTP responses by the API layer."""


class DomainError(Exception):
    """Base class for business-rule violations."""

    status_code = 400
    error_code = "domain_error"

    # Optional structured payload (e.g. a per-row import report) — the API
    # handler includes it on the error body when present.
    details: dict | None = None


class NotFoundError(DomainError):
    status_code = 404
    error_code = "not_found"


class InvalidTransitionError(DomainError):
    status_code = 409
    error_code = "invalid_transition"


class NoDriverAvailableError(DomainError):
    status_code = 409
    error_code = "no_driver_available"


class CouponInvalidError(DomainError):
    """The uniform coupon rejection (#159 item 3): unknown, inactive,
    expired or budget-spent all read identically — codes cannot be probed."""

    status_code = 422
    error_code = "coupon_invalid"


class PaymentError(DomainError):
    """The provider refused the charge (sandbox: non-positive amount)."""

    status_code = 409
    error_code = "payment_rejected"


class GeocodeAddressError(DomainError):
    """The geocoder could not resolve the address (#232). A 422 on the
    request path: it is the caller's data problem, and an unresolvable
    address must never silently become a wrong-coordinate delivery."""

    status_code = 422
    error_code = "address_unresolvable"


class GeocoderUnavailableError(DomainError):
    """The geocoder provider could not be reached (#232). A 503: the
    caller should retry, or send dropoff coordinates instead — the
    request path degrades loudly instead of guessing."""

    status_code = 503
    error_code = "geocoder_unavailable"


class ImportRejectedError(DomainError):
    """A bulk-import file was rejected all-or-nothing (#223): at least one
    row failed validation or geocoding, so NOTHING was imported. The
    per-row report rides on `details` — the client fixes exactly the rows
    named and re-submits."""

    status_code = 422
    error_code = "import_rejected"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details
