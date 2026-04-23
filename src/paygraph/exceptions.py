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
        gateway_name: Name of the gateway that initiated the approval.
            Pass this back to ``complete_spend()`` to resolve the correct gateway.
    """

    def __init__(
        self,
        request_id: str,
        amount: float,
        vendor: str,
        gateway_name: str = "default",
    ) -> None:
        self.request_id = request_id
        self.amount = amount
        self.vendor = vendor
        self.gateway_name = gateway_name
        super().__init__(
            f"Spend of ${amount:.2f} for '{vendor}' requires human approval "
            f"(request_id={request_id})"
        )
