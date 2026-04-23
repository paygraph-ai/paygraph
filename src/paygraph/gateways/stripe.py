import httpx

from paygraph.exceptions import GatewayError
from paygraph.gateways.base import BaseGateway, CardResult

_DEFAULT_BILLING = {
    "line1": "1 Market St",
    "city": "San Francisco",
    "state": "CA",
    "postal_code": "94105",
    "country": "US",
}


class StripeCardGateway(BaseGateway):
    """Stripe Issuing gateway that creates real virtual cards.

    Automatically detects test vs live mode from the API key prefix
    (``sk_test_`` or ``sk_live_``). Creates or reuses a Stripe cardholder.

    Args:
        api_key: Stripe secret key (must start with ``sk_test_`` or ``sk_live_``).
        cardholder_id: Existing Stripe cardholder ID to use. If None, one is
            created or reused automatically.
        currency: ISO currency code (default ``"usd"``).
        billing_address: Cardholder billing address dict. Uses a San Francisco
            default if not provided.
        single_use: If True (default), a new card is created per transaction.
            If False, reuses the same card and updates the spending limit.
        allowed_mccs: Stripe MCC allowlist applied at the card level.
        blocked_mccs: Stripe MCC blocklist applied at the card level.
    """

    API_BASE = "https://api.stripe.com"

    def __init__(
        self,
        api_key: str,
        cardholder_id: str | None = None,
        currency: str = "usd",
        billing_address: dict[str, str] | None = None,
        single_use: bool = True,
        allowed_mccs: list[str] | None = None,
        blocked_mccs: list[str] | None = None,
    ):
        if api_key.startswith("sk_test_"):
            self._gateway_type = "stripe_test"
        elif api_key.startswith("sk_live_"):
            self._gateway_type = "stripe_live"
        else:
            raise GatewayError(
                "Invalid Stripe API key — must start with sk_test_ or sk_live_"
            )

        self._client = httpx.Client(
            base_url=self.API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        self._cardholder_id = cardholder_id
        self._currency = currency.lower()
        self._billing = billing_address or _DEFAULT_BILLING
        self._single_use = single_use
        self._allowed_mccs = allowed_mccs
        self._blocked_mccs = blocked_mccs
        self._card_cache: dict[str, str] | None = (
            None  # cached card detail for reuse mode
        )

    def _find_existing_cardholder(self) -> str | None:
        """Look for an existing PayGraph Agent cardholder."""
        try:
            resp = self._client.get(
                "/v1/issuing/cardholders",
                params={"email": "agent@paygraph.dev", "status": "active", "limit": 1},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None

        data = resp.json().get("data", [])
        if data:
            return data[0]["id"]
        return None

    def _ensure_cardholder(self) -> str:
        if self._cardholder_id:
            return self._cardholder_id

        # Reuse existing cardholder if one exists
        existing = self._find_existing_cardholder()
        if existing:
            self._cardholder_id = existing
            return self._cardholder_id

        try:
            resp = self._client.post(
                "/v1/issuing/cardholders",
                data={
                    "type": "individual",
                    "name": "PayGraph Agent",
                    "email": "agent@paygraph.dev",
                    "status": "active",
                    "individual[first_name]": "PayGraph",
                    "individual[last_name]": "Agent",
                    "phone_number": "+18005550000",
                    **{f"billing[address][{k}]": v for k, v in self._billing.items()},
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = e.response.json().get("error", {}).get("message", str(e))
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e

        self._cardholder_id = resp.json()["id"]
        return self._cardholder_id

    def _create_card(
        self, cardholder_id: str, amount_cents: int, vendor: str, memo: str
    ) -> dict:
        """Create a new Stripe Issuing card and return its expanded detail."""
        card_data: dict[str, str] = {
            "type": "virtual",
            "cardholder": cardholder_id,
            "currency": self._currency,
            "status": "active",
            "spending_controls[spending_limits][0][amount]": str(amount_cents),
            "spending_controls[spending_limits][0][interval]": "all_time",
        }
        if self._allowed_mccs:
            for i, mcc in enumerate(self._allowed_mccs):
                card_data[f"spending_controls[allowed_categories][{i}]"] = mcc
        if self._blocked_mccs:
            for i, mcc in enumerate(self._blocked_mccs):
                card_data[f"spending_controls[blocked_categories][{i}]"] = mcc
        if memo:
            card_data["metadata[memo]"] = memo[:500]
        if vendor:
            card_data["metadata[vendor]"] = vendor[:100]

        try:
            resp = self._client.post("/v1/issuing/cards", data=card_data)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = e.response.json().get("error", {}).get("message", str(e))
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e

        card_id = resp.json()["id"]
        return self._get_card_detail(card_id)

    def _get_card_detail(self, card_id: str) -> dict:
        """Fetch expanded card detail (PAN, CVC)."""
        try:
            resp = self._client.get(
                f"/v1/issuing/cards/{card_id}",
                params={"expand[]": ["number", "cvc"]},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = e.response.json().get("error", {}).get("message", str(e))
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e
        return resp.json()

    def _update_spending_limit(self, card_id: str, amount_cents: int) -> None:
        """Update the spending limit on an existing card."""
        try:
            resp = self._client.post(
                f"/v1/issuing/cards/{card_id}",
                data={
                    "spending_controls[spending_limits][0][amount]": str(amount_cents),
                    "spending_controls[spending_limits][0][interval]": "all_time",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = e.response.json().get("error", {}).get("message", str(e))
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e

    def execute(self, amount_cents: int, vendor: str, memo: str) -> CardResult:
        """Create a Stripe Issuing virtual card with the given spend limit.

        Calls the Stripe ``/v1/issuing/cards`` API to mint a new card
        (or update the existing card's limit if ``single_use`` is False).

        Args:
            amount_cents: Spend limit in cents.
            vendor: Name of the vendor (stored in card metadata).
            memo: Justification (stored in card metadata, truncated to 500 chars).

        Returns:
            A ``CardResult`` with the real PAN, CVV, and expiry.

        Raises:
            GatewayError: If the Stripe API call fails.
        """
        cardholder_id = self._ensure_cardholder()

        if self._single_use or self._card_cache is None:
            detail = self._create_card(cardholder_id, amount_cents, vendor, memo)
            if not self._single_use:
                self._card_cache = detail
        else:
            detail = self._card_cache
            self._update_spending_limit(detail["id"], amount_cents)

        exp_month = detail["exp_month"]
        exp_year = detail["exp_year"]

        return CardResult(
            pan=detail["number"],
            cvv=detail["cvc"],
            expiry=f"{exp_month:02d}/{exp_year % 100:02d}",
            spend_limit_cents=amount_cents,
            amount_cents=amount_cents,
            gateway_ref=detail["id"],
            gateway_type=self._gateway_type,
        )

    def revoke(self, gateway_ref: str) -> bool:
        """Cancel a Stripe Issuing card.

        Sets the card status to ``"canceled"`` via the Stripe API.

        Args:
            gateway_ref: The Stripe card ID (``ic_...``).

        Returns:
            True if the card was canceled, False if not found (404).

        Raises:
            GatewayError: If the Stripe API call fails (non-404).
        """
        try:
            resp = self._client.post(
                f"/v1/issuing/cards/{gateway_ref}",
                data={"status": "canceled"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            msg = e.response.json().get("error", {}).get("message", str(e))
            raise GatewayError(f"Stripe API error: {msg}") from e
        except httpx.HTTPError as e:
            raise GatewayError(f"Stripe API unreachable: {e}") from e
        return True
