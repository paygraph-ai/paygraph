from unittest.mock import MagicMock, patch

import httpx
import pytest

from paygraph.exceptions import GatewayError
from paygraph.gateways.stripe_mpp import _ISSUE_PATH, StripeMPPGateway, _deactivate_path


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


_TOKEN_JSON = {"id": "spt_test_issue_abc", "object": "shared_payment.issued_token"}


class TestKeyDetection:
    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_test_key(self, mock_client_cls):
        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_test_visa",
            grantee="profile_seller",
        )
        assert gw._gateway_type == "stripe_mpp_test"

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_live_key(self, mock_client_cls):
        gw = StripeMPPGateway(
            api_key="sk_live_xxx",
            payment_method="pm_test_visa",
            grantee="profile_seller",
        )
        assert gw._gateway_type == "stripe_mpp_live"

    def test_invalid_key(self):
        with pytest.raises(GatewayError, match="Invalid Stripe API key"):
            StripeMPPGateway(
                api_key="bad_key",
                payment_method="pm_x",
                grantee="profile_x",
            )

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_invalid_payment_method(self, mock_client_cls):
        with pytest.raises(GatewayError, match="pm_"):
            StripeMPPGateway(
                api_key="sk_test_x",
                payment_method="bad",
                grantee="profile_x",
            )

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_empty_grantee(self, mock_client_cls):
        with pytest.raises(GatewayError, match="grantee"):
            StripeMPPGateway(
                api_key="sk_test_x",
                payment_method="pm_x",
                grantee="",
            )

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_expires_in_seconds_must_be_positive(self, mock_client_cls):
        for bad in (0, -1):
            with pytest.raises(GatewayError, match="expires_in_seconds"):
                StripeMPPGateway(
                    api_key="sk_test_x",
                    payment_method="pm_x",
                    grantee="profile_x",
                    expires_in_seconds=bad,
                )


class TestExecuteSpend:
    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200, _TOKEN_JSON)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_card_visa",
            grantee="profile_123live",
            currency="usd",
            expires_in_seconds=7200,
        )
        with patch(
            "paygraph.gateways.stripe_mpp.time.time", return_value=1_700_000_000
        ):
            card = gw.execute_spend(420, "Anthropic", "API credits")

        assert card.pan == "SPT_NO_PAN"
        assert card.cvv == "N/A"
        assert card.expiry == "--/--"
        assert card.spend_limit_cents == 420
        assert card.gateway_ref == "spt_test_issue_abc"
        assert card.gateway_type == "stripe_mpp_test"

        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert call[0][0] == _ISSUE_PATH
        data = call[1]["data"]
        assert data["payment_method"] == "pm_card_visa"
        assert data["grantee"] == "profile_123live"
        assert data["usage_limits[currency]"] == "usd"
        assert data["usage_limits[max_amount]"] == "420"
        assert data["usage_limits[expires_at]"] == str(1_700_000_000 + 7200)
        assert data["metadata[vendor]"] == "Anthropic"
        assert data["metadata[memo]"] == "API credits"

        headers = mock_client_cls.call_args[1]["headers"]
        assert headers["Stripe-Version"] == "2026-03-04.preview"

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_metadata_truncation(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200, _TOKEN_JSON)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        long_vendor = "V" * 150
        long_memo = "M" * 600
        gw.execute_spend(100, long_vendor, long_memo)
        data = mock_client.post.call_args[1]["data"]
        assert len(data["metadata[vendor]"]) == 100
        assert len(data["metadata[memo]"]) == 500

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_http_400(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(
            400, {"error": {"message": "invalid_grantee"}}
        )

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="Stripe API error: invalid_grantee"):
            gw.execute_spend(100, "v", "m")

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_http_error_with_non_json_body(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        response = _mock_response(400)
        response.json.side_effect = ValueError("not json")
        mock_client.post.return_value = response

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="Stripe API error:"):
            gw.execute_spend(100, "v", "m")

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_missing_id_in_response(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200, {"object": "weird"})

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="no token id"):
            gw.execute_spend(100, "v", "m")

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_network_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="Stripe API unreachable"):
            gw.execute_spend(100, "v", "m")

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_omits_empty_vendor_and_memo_metadata(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200, _TOKEN_JSON)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        gw.execute_spend(100, "", "")
        data = mock_client.post.call_args[1]["data"]
        assert "metadata[vendor]" not in data
        assert "metadata[memo]" not in data


class TestRevoke:
    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        assert gw.revoke("spt_test_issue_abc") is True
        mock_client.post.assert_called_once_with(_deactivate_path("spt_test_issue_abc"))

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_404_returns_false(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(404)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        assert gw.revoke("nonexistent") is False

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_500_raises_gateway_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="Stripe API error"):
            gw.revoke("spt_x")

    @patch("paygraph.gateways.stripe_mpp.httpx.Client")
    def test_non_json_error_response_raises_gateway_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        response = _mock_response(500)
        response.json.side_effect = ValueError("not json")
        mock_client.post.return_value = response

        gw = StripeMPPGateway(
            api_key="sk_test_xxx",
            payment_method="pm_x",
            grantee="profile_x",
        )
        with pytest.raises(GatewayError, match="Stripe API error:"):
            gw.revoke("spt_x")
