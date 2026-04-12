import json
import tempfile

import pytest

from paygraph.exceptions import GatewayError, PolicyViolationError, SpendDeniedError
from paygraph.gateways.mock_x402 import MockX402Gateway
from paygraph.gateways.x402 import X402Receipt
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet


def _make_wallet(x402_gateway=None, policy=None, **kwargs) -> tuple[AgentWallet, str]:
    """Create a wallet with x402 gateway and a temp audit file."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return (
        AgentWallet(
            x402_gateway=x402_gateway or MockX402Gateway(auto_approve=True),
            policy=policy or SpendPolicy(),
            log_path=f.name,
            verbose=False,
            **kwargs,
        ),
        f.name,
    )


def _read_audit(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class TestMockX402Gateway:
    def test_returns_receipt(self):
        gw = MockX402Gateway(auto_approve=True, response_body='{"result": "ok"}')
        receipt = gw.execute_x402(
            "https://api.example.com/data", 500, "ExampleAPI", "need data"
        )
        assert isinstance(receipt, X402Receipt)
        assert receipt.url == "https://api.example.com/data"
        assert receipt.amount_cents == 500
        assert receipt.gateway_type == "x402"
        assert receipt.response_body == '{"result": "ok"}'
        assert receipt.transaction_hash.startswith("0xmock_")
        assert receipt.gateway_ref == receipt.transaction_hash

    def test_human_denial(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockX402Gateway(auto_approve=False)
        with pytest.raises(SpendDeniedError, match="Human denied"):
            gw.execute_x402("https://api.example.com", 100, "vendor", "reason")

    def test_human_approval(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        gw = MockX402Gateway(auto_approve=False)
        receipt = gw.execute_x402("https://api.example.com", 100, "vendor", "reason")
        assert receipt.status_code == 200


class TestRequestX402Approved:
    def test_returns_response_body(self):
        gw = MockX402Gateway(auto_approve=True, response_body='{"data": 42}')
        wallet, _ = _make_wallet(x402_gateway=gw)
        result = wallet.request_x402(
            "https://api.example.com/data", 4.20, "ExampleAPI", "need data"
        )
        assert result == '{"data": 42}'

    def test_audit_records_approval(self):
        gw = MockX402Gateway(auto_approve=True)
        wallet, path = _make_wallet(x402_gateway=gw)
        wallet.request_x402("https://api.example.com", 4.20, "ExampleAPI", "need data")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "approved"
        assert records[0]["amount"] == 4.20
        assert records[0]["vendor"] == "ExampleAPI"
        assert records[0]["gateway_type"] == "x402"
        assert records[0]["gateway_ref"] is not None


class TestRequestX402PolicyDenial:
    def test_raises_policy_violation(self):
        wallet, _ = _make_wallet(policy=SpendPolicy(max_transaction=5.0))
        with pytest.raises(PolicyViolationError, match="exceeds limit"):
            wallet.request_x402("https://api.example.com", 100.0, "vendor", "reason")

    def test_audit_records_denial(self):
        wallet, path = _make_wallet(policy=SpendPolicy(max_transaction=5.0))
        with pytest.raises(PolicyViolationError):
            wallet.request_x402("https://api.example.com", 100.0, "vendor", "reason")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "denied"
        assert records[0]["gateway_ref"] is None


class TestRequestX402HumanDenial:
    def test_raises_spend_denied(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockX402Gateway(auto_approve=False)
        wallet, _ = _make_wallet(x402_gateway=gw)
        with pytest.raises(SpendDeniedError):
            wallet.request_x402("https://api.example.com", 4.20, "vendor", "reason")

    def test_audit_records_human_denial(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockX402Gateway(auto_approve=False)
        wallet, path = _make_wallet(x402_gateway=gw)
        with pytest.raises(SpendDeniedError):
            wallet.request_x402("https://api.example.com", 4.20, "vendor", "reason")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "denied"
        assert "Human denied" in records[0]["denial_reason"]


class TestRequestX402GatewayError:
    def test_raises_gateway_error(self):
        class BrokenX402Gateway:
            def execute_x402(self, *args, **kwargs):
                raise RuntimeError("connection refused")

        wallet, _ = _make_wallet(x402_gateway=BrokenX402Gateway())
        with pytest.raises(GatewayError, match="connection refused"):
            wallet.request_x402("https://api.example.com", 4.20, "vendor", "reason")


class TestRequestX402NoGateway:
    def test_raises_when_no_x402_gateway(self):
        wallet, _ = _make_wallet(x402_gateway=None)
        # Override — _make_wallet defaults to MockX402Gateway, so make one without
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        wallet = AgentWallet(
            policy=SpendPolicy(),
            log_path=f.name,
            verbose=False,
        )
        with pytest.raises(GatewayError, match="No x402 gateway configured"):
            wallet.request_x402("https://api.example.com", 4.20, "vendor", "reason")


class TestRequestX402BudgetTracking:
    def test_daily_budget_tracked(self):
        wallet, _ = _make_wallet(
            policy=SpendPolicy(daily_budget=10.0, max_transaction=50.0)
        )
        wallet.request_x402("https://api.example.com", 6.0, "vendor", "reason")
        wallet.request_x402("https://api.example.com", 3.0, "vendor", "reason")
        with pytest.raises(PolicyViolationError, match="Daily budget"):
            wallet.request_x402("https://api.example.com", 5.0, "vendor", "reason")


class TestRequestX402HttpMethod:
    def test_post_method(self):
        gw = MockX402Gateway(auto_approve=True, response_body='{"created": true}')
        wallet, _ = _make_wallet(x402_gateway=gw)
        result = wallet.request_x402(
            "https://api.example.com/create",
            2.0,
            "ExampleAPI",
            "creating resource",
            method="POST",
            body='{"name": "test"}',
        )
        assert result == '{"created": true}'
