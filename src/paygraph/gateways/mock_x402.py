import secrets

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.base import BaseGateway, X402Result

# Deprecated alias — kept for one release cycle
X402Receipt = X402Result


class MockX402Gateway(BaseGateway):
    """Mock x402 gateway for testing without blockchain access.

    Simulates the x402 payment flow by generating fake transaction hashes
    and returning configurable response bodies. Optionally prompts for
    human approval.

    Args:
        auto_approve: If True, skip the terminal approval prompt.
        response_body: Canned response body to return.
        status_code: HTTP status code of the mock response.
        content_type: Content-Type of the mock response.
    """

    def __init__(
        self,
        auto_approve: bool = False,
        response_body: str = "{}",
        status_code: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.auto_approve = auto_approve
        self.response_body = response_body
        self.status_code = status_code
        self.content_type = content_type
        self._receipts: dict[str, X402Result] = {}

    async def execute_async(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Async variant — delegates to :meth:`execute`."""
        return self.execute(
            amount_cents, vendor, memo, url=url, method=method, headers=headers, body=body
        )

    def execute(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Simulate an x402 payment, optionally prompting for approval.

        Args:
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor.
            memo: Justification for the payment.
            url: The x402-enabled endpoint URL.
            method: HTTP method (ignored in mock).
            headers: Optional HTTP headers (ignored in mock).
            body: Optional request body (ignored in mock).

        Returns:
            An ``X402Result`` with a fake transaction hash and the
            configured response body.

        Raises:
            SpendDeniedError: If the human denies the approval prompt.
        """
        amount_dollars = amount_cents / 100
        if not self.auto_approve:
            response = input(
                f"[PayGraph] Approve x402 payment ${amount_dollars:.2f} for {vendor}? [Y/n]: "
            )
            if response.strip().lower() not in ("", "y", "yes"):
                raise SpendDeniedError(
                    f"Human denied x402 payment of ${amount_dollars:.2f} for {vendor}"
                )

        tx_hash = f"0xmock_{secrets.token_hex(16)}"
        receipt = X402Result(
            url=url,
            amount_cents=amount_cents,
            network="eip155:8453",
            transaction_hash=tx_hash,
            payer="0xMockPayer1234567890abcdef",
            gateway_ref=tx_hash,
            gateway_type="x402",
            status_code=self.status_code,
            response_body=self.response_body,
            content_type=self.content_type,
        )
        self._receipts[tx_hash] = receipt
        return receipt
