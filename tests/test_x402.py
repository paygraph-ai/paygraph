import asyncio
import json
import tempfile

import pytest

from paygraph.exceptions import GatewayError, PolicyViolationError, SpendDeniedError
from paygraph.gateways.base import BaseGateway, X402Result
from paygraph.gateways.mock_x402 import MockX402Gateway
from paygraph.gateways.x402 import X402Gateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet

# ---------------------------------------------------------------------------
# Helpers shared by X402Gateway dispatch tests
# ---------------------------------------------------------------------------


def _fake_receipt(
    url: str = "https://api.example.com", amount_cents: int = 100
) -> X402Result:
    return X402Result(
        url=url,
        amount_cents=amount_cents,
        network="eip155:8453",
        transaction_hash="0xfakehash",
        payer="0xFakePayer",
        gateway_ref="0xfakehash",
        gateway_type="x402",
    )


class _FakeX402Gateway(X402Gateway):
    """Minimal X402Gateway that skips SDK imports and returns a fake receipt."""

    def __init__(self) -> None:
        self._payer = "0xFakePayer"

    async def execute_async(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        **kwargs,
    ) -> X402Result:
        url = kwargs.get("url", "https://api.example.com")
        return _fake_receipt(url, amount_cents)


def _make_wallet(
    x402_gateway=None, policy=None, **kwargs
) -> tuple[AgentWallet, str]:
    """Create a wallet with x402 gateway and a temp audit file."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    gw = x402_gateway if x402_gateway is not None else MockX402Gateway(auto_approve=True)
    return (
        AgentWallet(
            gateways={"default": MockX402Gateway(auto_approve=True), "x402": gw},
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
        receipt = gw.execute(
            500, "ExampleAPI", "need data",
            url="https://api.example.com/data",
        )
        assert isinstance(receipt, X402Result)
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
            gw.execute(100, "vendor", "reason", url="https://api.example.com")

    def test_human_approval(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        gw = MockX402Gateway(auto_approve=False)
        receipt = gw.execute(100, "vendor", "reason", url="https://api.example.com")
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
        class BrokenX402Gateway(BaseGateway):
            def execute(self, amount_cents, vendor, memo, **kwargs):
                raise RuntimeError("connection refused")

        wallet, _ = _make_wallet(x402_gateway=BrokenX402Gateway())
        with pytest.raises(GatewayError, match="connection refused"):
            wallet.request_x402("https://api.example.com", 4.20, "vendor", "reason")


class TestRequestX402NoGateway:
    def test_raises_when_no_x402_gateway(self):
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        wallet = AgentWallet(
            policy=SpendPolicy(),
            log_path=f.name,
            verbose=False,
        )
        with pytest.raises(GatewayError, match="No gateway named 'x402'"):
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


# ---------------------------------------------------------------------------
# X402Gateway sync dispatch tests
# ---------------------------------------------------------------------------


class TestX402GatewaySyncDispatch:
    """execute() must work both with and without a running event loop."""

    def test_sync_call_without_running_loop(self):
        """Standard sync call (scripts, CLI) should return a receipt via asyncio.run()."""
        gw = _FakeX402Gateway()
        result = gw.execute(100, "vendor", "memo", url="https://api.example.com")
        assert isinstance(result, X402Result)
        assert result.url == "https://api.example.com"
        assert result.amount_cents == 100
        assert result.transaction_hash == "0xfakehash"

    def test_sync_call_from_running_loop_does_not_raise(self):
        """execute() called from within a running loop must not raise RuntimeError.

        Simulates the LangGraph / FastAPI scenario where an event loop is already
        running in the current thread when the sync method is invoked.
        """
        gw = _FakeX402Gateway()

        async def call_sync_inside_loop() -> X402Result:
            # Deliberately calls the *sync* wrapper from inside a coroutine to
            # replicate the nested-loop scenario.
            return gw.execute(200, "vendor", "memo", url="https://api.example.com")

        result = asyncio.run(call_sync_inside_loop())
        assert isinstance(result, X402Result)
        assert result.amount_cents == 200

    def test_sync_call_from_running_loop_returns_correct_receipt(self):
        """Receipt fields survive the thread-pool round-trip intact."""
        gw = _FakeX402Gateway()

        async def call_sync_inside_loop() -> X402Result:
            return gw.execute(
                499, "PaidAPI", "data", url="https://paid-api.example.com"
            )

        result = asyncio.run(call_sync_inside_loop())
        assert result.url == "https://paid-api.example.com"
        assert result.amount_cents == 499
        assert result.payer == "0xFakePayer"
        assert result.gateway_ref == "0xfakehash"

    def test_async_method_available(self):
        """execute_async() is directly awaitable for fully-async callers."""
        gw = _FakeX402Gateway()

        async def run():
            return await gw.execute_async(
                50, "vendor", "memo", url="https://api.example.com"
            )

        result = asyncio.run(run())
        assert isinstance(result, X402Result)
        assert result.amount_cents == 50
