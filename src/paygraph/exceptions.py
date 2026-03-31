class PayGraphError(Exception):
    """Base exception for all PayGraph errors."""


class SpendDeniedError(PayGraphError):
    """Human denied the spend request via MockGateway."""


class PolicyViolationError(PayGraphError):
    """Policy engine rejected the spend request."""


class GatewayError(PayGraphError):
    """Gateway API call failed."""
