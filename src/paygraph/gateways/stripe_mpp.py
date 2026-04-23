"""Stripe Shared Payment Token (SPT) gateway for agent-side spend.

Issues scoped shared payment tokens so MPP-enabled sellers can complete
``PaymentIntent`` with ``shared_payment_granted_token`` instead of using
Stripe Issuing virtual cards.

Requires Stripe **machine payments / agentic commerce** access and API version
``2026-03-04.preview``. See https://docs.stripe.com/payments/machine/mpp and
https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens

``CardResult`` is used for API compatibility only: SPTs have no PAN/CVV.
Integrators should use ``gateway_ref`` (the ``spt_...`` id) when talking to
merchants, not the sentinel ``pan``/``cvv``/``expiry`` fields.
"""

from __future__ import annotations

import time

import httpx

from paygraph.exceptions import GatewayError
from paygraph.gateways.base import BaseGateway, CardResult

# Preview REST paths — confirm against Stripe preview API reference if calls fail.
_ISSUE_PATH = "/v1/shared_payment/issued_tokens"


def _deactivate_path(token_id: str) -> str:
    return f"/v1/shared_payment/issued_tokens/{token_id}/deactivate"


def _status_error_message(exc: httpx.HTTPStatusError) -> str:
    """Extract Stripe error text safely for non-JSON responses."""
    try:
        payload = exc.response.json()
        if isinstance(payload, dict):
            return payload.get("error", {}).get("message", str(exc))
    except Exception:
        pass
    return str(exc)


class StripeMPPGateway(BaseGateway):
    """Issue Stripe Shared Payment Tokens (SPTs) instead of virtual cards.

    Use this when the seller accepts MPP / agentic commerce and charges via
    a granted SPT on their ``PaymentIntent``, rather than card PAN entry.

    Args:
        api_key: Stripe secret key (``sk_test_...`` or ``sk_live_...``).
        payment_method: Saved ``pm_...`` on the agent platform (customer's method).
        grantee: Seller identifier — typically a Stripe Profile ``profile_...``
            (live). The preview API may accept other grantee forms; see Stripe docs.
        currency: ISO currency code (default ``\"usd\"``).
        expires_in_seconds: Token lifetime from issuance in seconds; must be > 0.

    Note:
        Machine payments may require account enablement:
        https://docs.stripe.com/payments/machine#sign-up
    """

    API_BASE = "https://api.stripe.com"
    API_VERSION = "2026-03-04.preview"

    _SPT_PAN = "SPT_NO_PAN"
    _SPT_CVV = "N/A"
    _SPT_EXPIRY = "--/--"

    def __init__(
        self,
        api_key: str,
        payment_method: str,
        grantee: str,
        currency: str = "usd",
        expires_in_seconds: int = 3600,
    ) -> None:
        if api_key.startswith("sk_test_"):
            self._gateway_type = "stripe_mpp_test"
        elif api_key.startswith("sk_live_"):
            self._gateway_type = "stripe_mpp_live"
        else:
            raise GatewayError(
                "Invalid Stripe API key — must start with sk_test_ or sk_live_"
            )

        if not payment_method or not payment_method.startswith("pm_"):
            raise GatewayError(
                "payment_method must be a Stripe PaymentMethod id (pm_...)"
            )
        if not grantee:
            raise GatewayError("grantee must be non-empty (e.g. profile_...)")
        if expires_in_seconds <= 0:
            raise GatewayError("expires_in_seconds must be a positive integer")

        self._client = httpx.Client(
            base_url=self.API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Stripe-Version": self.API_VERSION,
            },
            timeout=30,
        )
        self._payment_method = payment_method
        self._grantee = grantee
        self._currency = currency.lower()
        self._expires_in_seconds = expires_in_seconds

    def execute(self, amount_cents: int, vendor: str, memo: str) -> CardResult:
        """Issue an SPT with usage limits matching this spend request.

        Metadata truncation matches ``StripeCardGateway`` (vendor 100 chars, memo 500).
        """
        expires_at = int(time.time()) + self._expires_in_seconds
        data: dict[str, str] = {
            "payment_method": self._payment_method,
            "grantee": self._grantee,
            "usage_limits[currency]": self._currency,
            "usage_limits[max_amount]": str(amount_cents),
            "usage_limits[expires_at]": str(expires_at),
        }
        if vendor:
            data["metadata[vendor]"] = vendor[:100]
        if memo:
            data["metadata[memo]"] = memo[:500]

        try:
            resp = self._client.post(_ISSUE_PATH, data=data)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = _status_error_message(e)
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e

        body = resp.json()
        token_id = body.get("id")
        if not token_id:
            raise GatewayError(
                "Stripe API returned no token id for shared payment issuance"
            )

        return CardResult(
            pan=self._SPT_PAN,
            cvv=self._SPT_CVV,
            expiry=self._SPT_EXPIRY,
            spend_limit_cents=amount_cents,
            amount_cents=amount_cents,
            gateway_ref=token_id,
            gateway_type=self._gateway_type,
        )

    def revoke(self, gateway_ref: str) -> bool:
        """Deactivate an issued SPT so it can no longer be used."""
        try:
            resp = self._client.post(_deactivate_path(gateway_ref))
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            msg = _status_error_message(e)
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e
        return True
