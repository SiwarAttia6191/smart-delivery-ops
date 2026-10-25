"""Domain exceptions mapped to HTTP responses by the API layer."""


class DomainError(Exception):
    """Base class for business-rule violations."""

    status_code = 400
    error_code = "domain_error"


class NotFoundError(DomainError):
    status_code = 404
    error_code = "not_found"


class InvalidTransitionError(DomainError):
    status_code = 409
    error_code = "invalid_transition"


class NoDriverAvailableError(DomainError):
    status_code = 409
    error_code = "no_driver_available"
