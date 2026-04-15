from unittest.mock import MagicMock, patch

import httpx
import pytest

from paygraph.exceptions import GatewayError
from paygraph.gateways.stripe import StripeCardGateway


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


_CARDHOLDER_JSON = {"id": "ich_test_abc123"}

_CARD_CREATE_JSON = {"id": "ic_test_card456"}

_CARD_DETAIL_JSON = {
    "id": "ic_test_card456",
    "number": "4242424242424242",
    "cvc": "789",
    "exp_month": 3,
    "exp_year": 2028,
}


class TestKeyDetection:
    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_test_key(self, mock_client_cls):
        gw = StripeCardGateway(api_key="sk_test_xxx")
        assert gw._gateway_type == "stripe_test"

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_live_key(self, mock_client_cls):
        gw = StripeCardGateway(api_key="sk_live_xxx")
        assert gw._gateway_type == "stripe_live"

    def test_invalid_key(self):
        with pytest.raises(GatewayError, match="Invalid Stripe API key"):
            StripeCardGateway(api_key="bad_key")


_CARDHOLDER_LIST_EMPTY = {"data": []}
_CARDHOLDER_LIST_FOUND = {"data": [{"id": "ich_existing_123"}]}


class TestCardholderCreation:
    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_auto_creates_cardholder_when_none_exist(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # GET list returns empty, then POST creates, then card create, then card detail
        mock_client.get.side_effect = [
            _mock_response(200, _CARDHOLDER_LIST_EMPTY),
            _mock_response(200, _CARD_DETAIL_JSON),
        ]
        mock_client.post.side_effect = [
            _mock_response(200, _CARDHOLDER_JSON),
            _mock_response(200, _CARD_CREATE_JSON),
        ]

        gw = StripeCardGateway(api_key="sk_test_xxx")
        gw.execute_spend(420, "Anthropic", "API credits")

        # Verify cardholder POST was called
        cardholder_posts = [
            c
            for c in mock_client.post.call_args_list
            if c[0][0] == "/v1/issuing/cardholders"
        ]
        assert len(cardholder_posts) == 1

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_reuses_existing_cardholder(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # GET list returns existing cardholder, then card detail
        mock_client.get.side_effect = [
            _mock_response(200, _CARDHOLDER_LIST_FOUND),
            _mock_response(200, _CARD_DETAIL_JSON),
        ]
        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)

        gw = StripeCardGateway(api_key="sk_test_xxx")
        gw.execute_spend(420, "Anthropic", "API credits")

        # No cardholder POST — reused existing
        cardholder_posts = [
            c
            for c in mock_client.post.call_args_list
            if c[0][0] == "/v1/issuing/cardholders"
        ]
        assert len(cardholder_posts) == 0
        assert gw._cardholder_id == "ich_existing_123"

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_caches_cardholder(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # GET list returns empty once, then card details twice
        mock_client.get.side_effect = [
            _mock_response(200, _CARDHOLDER_LIST_EMPTY),
            _mock_response(200, _CARD_DETAIL_JSON),
            _mock_response(200, _CARD_DETAIL_JSON),
        ]
        mock_client.post.side_effect = [
            _mock_response(200, _CARDHOLDER_JSON),
            _mock_response(200, _CARD_CREATE_JSON),
            _mock_response(200, _CARD_CREATE_JSON),
        ]

        gw = StripeCardGateway(api_key="sk_test_xxx")
        gw.execute_spend(420, "Anthropic", "API credits")
        gw.execute_spend(100, "OpenAI", "Tokens")

        # Cardholder POST only called once
        cardholder_posts = [
            c
            for c in mock_client.post.call_args_list
            if c[0][0] == "/v1/issuing/cardholders"
        ]
        assert len(cardholder_posts) == 1

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_uses_provided_cardholder(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(api_key="sk_test_xxx", cardholder_id="ich_xxx")
        gw.execute_spend(420, "Anthropic", "API credits")

        # No cardholder list GET or POST
        get_calls = mock_client.get.call_args_list
        cardholder_gets = [c for c in get_calls if "/v1/issuing/cardholders" in str(c)]
        assert len(cardholder_gets) == 0
        post_calls = mock_client.post.call_args_list
        cardholder_posts = [
            c for c in post_calls if c[0][0] == "/v1/issuing/cardholders"
        ]
        assert len(cardholder_posts) == 0


class TestExecuteSpend:
    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(api_key="sk_test_xxx", cardholder_id="ich_xxx")
        card = gw.execute_spend(420, "Anthropic", "API credits")

        assert card.pan == "4242424242424242"
        assert card.cvv == "789"
        assert card.expiry == "03/28"
        assert card.spend_limit_cents == 420
        assert card.gateway_ref == "ic_test_card456"
        assert card.gateway_type == "stripe_test"

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_single_use_creates_new_card_each_time(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(
            api_key="sk_test_xxx", cardholder_id="ich_xxx", single_use=True
        )
        gw.execute_spend(420, "Anthropic", "API credits")
        gw.execute_spend(100, "OpenAI", "Tokens")

        # Two card creation POSTs
        card_posts = [
            c for c in mock_client.post.call_args_list if c[0][0] == "/v1/issuing/cards"
        ]
        assert len(card_posts) == 2

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_reuse_mode_creates_card_once(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.side_effect = [
            _mock_response(200, _CARD_CREATE_JSON),  # card create
            _mock_response(200),  # spending limit update
        ]
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(
            api_key="sk_test_xxx", cardholder_id="ich_xxx", single_use=False
        )
        card1 = gw.execute_spend(420, "Anthropic", "API credits")
        card2 = gw.execute_spend(100, "OpenAI", "Tokens")

        # Only one card creation POST
        card_creates = [
            c for c in mock_client.post.call_args_list if c[0][0] == "/v1/issuing/cards"
        ]
        assert len(card_creates) == 1

        # Second call updates spending limit on existing card
        limit_updates = [
            c
            for c in mock_client.post.call_args_list
            if c[0][0] == f"/v1/issuing/cards/{_CARD_DETAIL_JSON['id']}"
        ]
        assert len(limit_updates) == 1

        # Both return same card ref
        assert card1.gateway_ref == card2.gateway_ref

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_allowed_mccs(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(
            api_key="sk_test_xxx",
            cardholder_id="ich_xxx",
            allowed_mccs=["7372", "5734"],
        )
        gw.execute_spend(420, "Anthropic", "API credits")

        card_post = [
            c for c in mock_client.post.call_args_list if c[0][0] == "/v1/issuing/cards"
        ][0]
        card_data = card_post[1]["data"]
        assert card_data["spending_controls[allowed_categories][0]"] == "7372"
        assert card_data["spending_controls[allowed_categories][1]"] == "5734"

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_blocked_mccs(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(
            api_key="sk_test_xxx",
            cardholder_id="ich_xxx",
            blocked_mccs=["7995", "5813"],
        )
        gw.execute_spend(420, "Anthropic", "API credits")

        card_post = [
            c for c in mock_client.post.call_args_list if c[0][0] == "/v1/issuing/cards"
        ][0]
        card_data = card_post[1]["data"]
        assert card_data["spending_controls[blocked_categories][0]"] == "7995"
        assert card_data["spending_controls[blocked_categories][1]"] == "5813"

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_no_mccs_by_default(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.post.return_value = _mock_response(200, _CARD_CREATE_JSON)
        mock_client.get.return_value = _mock_response(200, _CARD_DETAIL_JSON)

        gw = StripeCardGateway(api_key="sk_test_xxx", cardholder_id="ich_xxx")
        gw.execute_spend(420, "Anthropic", "API credits")

        card_post = [
            c for c in mock_client.post.call_args_list if c[0][0] == "/v1/issuing/cards"
        ][0]
        card_data = card_post[1]["data"]
        assert not any("allowed_categories" in k for k in card_data)
        assert not any("blocked_categories" in k for k in card_data)

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_http_400(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        error_resp = _mock_response(400, {"error": {"message": "Invalid card params"}})
        mock_client.post.return_value = error_resp

        gw = StripeCardGateway(api_key="sk_test_xxx", cardholder_id="ich_xxx")
        with pytest.raises(GatewayError, match="Stripe API error: Invalid card params"):
            gw.execute_spend(420, "vendor", "memo")

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_network_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        gw = StripeCardGateway(api_key="sk_test_xxx", cardholder_id="ich_xxx")
        with pytest.raises(GatewayError, match="Stripe API unreachable"):
            gw.execute_spend(420, "vendor", "memo")


class TestRevoke:
    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        gw = StripeCardGateway(api_key="sk_test_xxx")
        assert gw.revoke("ic_test_card456") is True

        mock_client.post.assert_called_once_with(
            "/v1/issuing/cards/ic_test_card456",
            data={"status": "canceled"},
        )

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_404_returns_false(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(404)

        gw = StripeCardGateway(api_key="sk_test_xxx")
        assert gw.revoke("nonexistent") is False

    @patch("paygraph.gateways.stripe.httpx.Client")
    def test_500_raises_gateway_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        gw = StripeCardGateway(api_key="sk_test_xxx")
        with pytest.raises(GatewayError, match="Stripe API error"):
            gw.revoke("ic_test_card456")
