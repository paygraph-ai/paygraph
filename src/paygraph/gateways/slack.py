import secrets
import time

import httpx

from paygraph.exceptions import GatewayError, HumanApprovalRequired, SpendDeniedError
from paygraph.gateways.base import BaseGateway, CardResult, SpendResult

DEFAULT_PENDING_TTL_SECONDS = 24 * 60 * 60


class SlackApprovalGateway(BaseGateway):
    """Gateway that fires a Slack webhook for human approval before spending.

    Wraps an inner gateway (e.g. ``MockGateway``, ``StripeCardGateway``).
    When ``request_approval()`` is called, it posts an Approve/Deny message
    to Slack and raises ``HumanApprovalRequired``. The caller must store the
    ``request_id`` and later call ``complete_spend()`` once the human responds.

    Example::

        from paygraph import AgentWallet, SpendPolicy
        from paygraph.gateways.slack import SlackApprovalGateway
        from paygraph.gateways.mock import MockGateway
        from paygraph.exceptions import HumanApprovalRequired

        gateway = SlackApprovalGateway(
            webhook_url="https://hooks.slack.com/services/...",
            inner_gateway=MockGateway(auto_approve=True),
        )
        wallet = AgentWallet(
            gateways=gateway,
            policy=SpendPolicy(require_human_approval_above=20.0),
        )

        try:
            wallet.request_spend(50.0, "Anthropic", "need tokens")
        except HumanApprovalRequired as e:
            # Store e.request_id and e.gateway_name — resume later when human responds
            result = wallet.complete_spend(
                e.request_id, approved=True, gateway=e.gateway_name
            )
    """

    def __init__(
        self,
        webhook_url: str,
        inner_gateway: BaseGateway,
        pending_ttl_seconds: int | None = DEFAULT_PENDING_TTL_SECONDS,
    ) -> None:
        """Initialise the Slack approval gateway.

        Args:
            webhook_url: Slack incoming webhook URL to post approval requests to.
            inner_gateway: Gateway used to execute the spend once approved.
            pending_ttl_seconds: How long (in seconds) a pending approval request
                stays valid before auto-denying. Defaults to 24h. Pass ``None``
                to disable expiry (entries live forever — discouraged for
                production use).
        """
        self.webhook_url = webhook_url
        self.inner_gateway = inner_gateway
        self.pending_ttl_seconds = pending_ttl_seconds
        self._pending: dict[str, dict] = {}

    def request_approval(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        justification: str | None = None,
    ) -> None:
        """Post an approval request to Slack and raise ``HumanApprovalRequired``.

        Posts a plain-text notification to the configured incoming webhook URL.
        Note: incoming webhooks do not support interactive callbacks — wire up
        a full Slack app with interactivity enabled if you need button responses.

        Args:
            amount_cents: Spend amount in cents.
            vendor: Vendor name.
            memo: Justification for the spend.
            justification: Original justification string (stored for audit log).

        Raises:
            HumanApprovalRequired: Always — after successfully posting to Slack.
            GatewayError: If the Slack webhook POST fails.
        """
        request_id = secrets.token_hex(8)
        amount_dollars = amount_cents / 100

        payload = {
            "text": (
                f"*PayGraph Approval Required*\n"
                f"Amount: *${amount_dollars:.2f}*\n"
                f"Vendor: *{vendor}*\n"
                f"Justification: {memo}\n"
                f"Request ID: `{request_id}`\n"
                f"Reply with `approve {request_id}` or `deny {request_id}`."
            ),
        }

        try:
            httpx.post(self.webhook_url, json=payload, timeout=10)
        except Exception as e:
            raise GatewayError(f"Slack webhook POST failed: {e}") from e

        self._pending[request_id] = {
            "amount_cents": amount_cents,
            "vendor": vendor,
            "memo": memo,
            "justification": justification,
            "created_at": time.monotonic(),
        }
        raise HumanApprovalRequired(request_id, amount_dollars, vendor)

    def _is_expired(self, pending: dict) -> bool:
        if self.pending_ttl_seconds is None:
            return False
        return time.monotonic() - pending["created_at"] > self.pending_ttl_seconds

    def purge_expired(self) -> int:
        """Drop all pending requests older than ``pending_ttl_seconds``.

        Returns:
            The number of requests purged.
        """
        if self.pending_ttl_seconds is None:
            return 0
        expired = [rid for rid, p in self._pending.items() if self._is_expired(p)]
        for rid in expired:
            del self._pending[rid]
        return len(expired)

    def execute(
        self, amount_cents: int, vendor: str, memo: str, **kwargs
    ) -> SpendResult:
        """Execute a spend directly via the inner gateway (below-threshold path).

        Args:
            amount_cents: Spend amount in cents.
            vendor: Vendor name.
            memo: Justification for the spend.

        Returns:
            A ``SpendResult`` from the inner gateway.
        """
        return self.inner_gateway.execute(amount_cents, vendor, memo, **kwargs)

    def complete_spend(self, request_id: str, approved: bool) -> CardResult:
        """Resume a pending spend after a human has responded in Slack.

        Args:
            request_id: The ``request_id`` from the ``HumanApprovalRequired``
                exception raised by ``request_approval()``.
            approved: ``True`` if the human approved, ``False`` if denied.

        Returns:
            A ``CardResult`` from the inner gateway if approved.

        Raises:
            SpendDeniedError: If ``approved`` is ``False`` or if the request
                has expired past ``pending_ttl_seconds``.
            KeyError: If ``request_id`` is unknown or already completed.
        """
        pending = self._pending.pop(request_id)
        if self._is_expired(pending):
            raise SpendDeniedError(
                f"Approval timed out for spend of "
                f"${pending['amount_cents'] / 100:.2f} for {pending['vendor']}"
            )
        if not approved:
            raise SpendDeniedError(
                f"Human denied spend of ${pending['amount_cents'] / 100:.2f} "
                f"for {pending['vendor']}"
            )
        return self.inner_gateway.execute(
            pending["amount_cents"], pending["vendor"], pending["memo"]
        )

    def get_pending(self, request_id: str) -> dict:
        """Return a pending request's metadata without removing it.

        Used by ``AgentWallet.complete_spend()`` to retrieve vendor/justification
        for audit logging before calling ``complete_spend()``. Expired entries
        are still returned so the caller can write a denial audit record;
        ``complete_spend()`` will then raise ``SpendDeniedError``.

        Raises:
            KeyError: If ``request_id`` is unknown.
        """
        return self._pending[request_id]

    def revoke(self, gateway_ref: str) -> bool:
        """Revoke a card via the inner gateway.

        Args:
            gateway_ref: The ``gateway_ref`` of the card to revoke.

        Returns:
            ``True`` if the card was revoked, ``False`` otherwise.
        """
        return self.inner_gateway.revoke(gateway_ref)
