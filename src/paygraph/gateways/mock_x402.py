import secrets

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.x402 import X402Receipt


class MockX402Gateway:
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
        self._receipts: dict[str, X402Receipt] = {}

    async def execute_x402_async(
        self,
        url: str,
        amount_cents: int,
        vendor: str,
        memo: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Receipt:
        return self.execute_x402(
            url, amount_cents, vendor, memo, method=method, headers=headers, body=body
        )

    def execute_x402(
        self,
        url: str,
        amount_cents: int,
        vendor: str,
        memo: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Receipt:
        """Simulate an x402 payment, optionally prompting for approval.

        Args:
            url: The endpoint URL.
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor.
            memo: Justification for the payment.
            method: HTTP method (ignored in mock).
            headers: Optional headers (ignored in mock).
            body: Optional body (ignored in mock).

        Returns:
            An ``X402Receipt`` with a fake transaction hash and the
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
        receipt = X402Receipt(
            url=url,
            amount_cents=amount_cents,
            network="eip155:8453",
            transaction_hash=tx_hash,
            payer="0xMockPayer1234567890abcdef",
            gateway_ref=tx_hash,
            status_code=self.status_code,
            response_body=self.response_body,
            content_type=self.content_type,
        )
        self._receipts[tx_hash] = receipt
        return receipt
