import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SpendResult:
    """Base result returned by any gateway after a successful spend.

    Attributes:
        amount_cents: Amount spent in cents.
        gateway_ref: Unique reference ID from the gateway.
        gateway_type: Gateway identifier (e.g. ``"mock"``, ``"stripe_test"``, ``"x402"``).
    """

    amount_cents: int
    gateway_ref: str
    gateway_type: str


@dataclass
class CardResult(SpendResult):
    """Result of a successful card spend (virtual card details).

    Attributes:
        pan: Primary Account Number (full card number). **Treat as sensitive.**
        cvv: Card Verification Value.
        expiry: Expiration date in ``MM/YY`` format.
        spend_limit_cents: Maximum spend limit in cents.
    """

    pan: str = ""
    cvv: str = ""
    expiry: str = ""
    spend_limit_cents: int = 0

    def redacted(self) -> "CardResult":
        """Return a copy with the PAN masked, showing only the last 4 digits.

        Returns:
            A new ``CardResult`` with ``pan`` set to ``****XXXX``.
        """
        return CardResult(
            pan=f"****{self.pan[-4:]}",
            cvv=self.cvv,
            expiry=self.expiry,
            spend_limit_cents=self.spend_limit_cents,
            amount_cents=self.amount_cents,
            gateway_ref=self.gateway_ref,
            gateway_type=self.gateway_type,
        )


@dataclass
class X402Result(SpendResult):
    """Result of a successful x402 payment.

    Attributes:
        url: The x402-enabled endpoint URL that was called.
        network: Blockchain network identifier (e.g. ``"eip155:8453"``).
        transaction_hash: On-chain transaction hash.
        payer: Wallet address of the payer.
        status_code: HTTP status code of the response.
        response_body: Body of the HTTP response from the paid endpoint.
        content_type: Content-Type header of the response.
    """

    url: str = ""
    network: str = ""
    transaction_hash: str = ""
    payer: str = ""
    status_code: int = 200
    response_body: str = ""
    content_type: str = "application/json"


# Deprecated aliases — kept for one release cycle
VirtualCard = CardResult


class BaseGateway(ABC):
    """Abstract base class for all payment gateways.

    Subclass this to implement a custom gateway. You must implement
    ``execute()``. Override ``execute_async()`` for native async support
    and ``revoke()`` for card-style gateways that support cancellation.
    """

    @abstractmethod
    def execute(
        self, amount_cents: int, vendor: str, memo: str, **kwargs
    ) -> SpendResult:
        """Execute a spend for the given amount.

        Subclasses should declare gateway-specific parameters as explicit
        keyword-only arguments rather than consuming ``**kwargs``. This
        ensures callers get immediate feedback on typos.

        Args:
            amount_cents: Spend amount in cents.
            vendor: Name of the vendor.
            memo: Justification or memo for the spend.

        Returns:
            A ``SpendResult`` (or subclass) with the transaction details.
        """
        ...

    async def execute_async(
        self, amount_cents: int, vendor: str, memo: str, **kwargs
    ) -> SpendResult:
        """Execute a spend asynchronously.

        Default implementation runs ``execute()`` in a thread pool.
        Override for native async support (e.g. x402 gateways).

        Args:
            amount_cents: Spend amount in cents.
            vendor: Name of the vendor.
            memo: Justification or memo for the spend.

        Returns:
            A ``SpendResult`` (or subclass) with the transaction details.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.execute(amount_cents, vendor, memo, **kwargs)
        )

    def revoke(self, gateway_ref: str) -> bool:
        """Revoke (cancel) a previously issued spend.

        Optional — card gateways override this, x402 gateways typically don't.

        Args:
            gateway_ref: The ``gateway_ref`` from the ``SpendResult``.

        Returns:
            True if successfully revoked, False if not found.

        Raises:
            NotImplementedError: If this gateway does not support revocation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support revoke"
        )
