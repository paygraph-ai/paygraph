class PayGraphError(Exception):
    """Base exception for all PayGraph errors."""


class SpendDeniedError(PayGraphError):
    """Human denied the spend request via MockGateway."""


class PolicyViolationError(PayGraphError):
    """Policy engine rejected the spend request."""


class GatewayError(PayGraphError):
    """Gateway API call failed."""


class HumanApprovalRequired(PayGraphError):
    """Spend requires human approval via Slack before proceeding.

    Attributes:
        request_id: Unique ID for this pending approval (use to resume).
        amount: Dollar amount awaiting approval.
        vendor: Vendor name awaiting approval.
    """

    def __init__(self, request_id: str, amount: float, vendor: str) -> None:
        self.request_id = request_id
        self.amount = amount
        self.vendor = vendor
        super().__init__(
            f"Spend of ${amount:.2f} for '{vendor}' requires human approval "
            f"(request_id={request_id})"
        )
