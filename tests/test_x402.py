import asyncio
import base64
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


# ---------------------------------------------------------------------------
# Settled-amount capture (issue #35)
# ---------------------------------------------------------------------------


class _SettlingX402Gateway(X402Gateway):
    """X402Gateway that returns a receipt with configurable settled amount."""

    def __init__(self, settled_atomic: str | None) -> None:
        self._payer = "0xFakePayer"
        self._settled_atomic = settled_atomic

    async def execute_async(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        **kwargs,
    ) -> X402Result:
        settled_cents = (
            int(self._settled_atomic) // 10_000
            if self._settled_atomic is not None
            else None
        )
        return X402Result(
            url=kwargs.get("url", "https://api.example.com"),
            amount_cents=amount_cents,
            network="eip155:8453",
            transaction_hash="0xfakehash",
            payer=self._payer,
            gateway_ref="0xfakehash",
            gateway_type="x402",
            settled_amount_atomic=self._settled_atomic,
            settled_amount_cents=settled_cents,
        )


class TestSettledAmountCapture:
    def test_settled_amount_commits_against_settled_not_requested(self):
        """Wallet commits the settled amount when the gateway reports one."""
        # Caller requests $1.00 = 100 cents. Facilitator settles $1.50 (150 cents
        # = 1_500_000 atomic USDC units). Budget must reflect $1.50.
        gw = _SettlingX402Gateway(settled_atomic="1500000")
        wallet, path = _make_wallet(
            x402_gateway=gw,
            policy=SpendPolicy(daily_budget=2.0, max_transaction=5.0),
        )
        wallet.request_x402("https://api.example.com", 1.0, "PaidAPI", "data")

        # Second call for $0.60 must fail — daily budget already used $1.50.
        with pytest.raises(PolicyViolationError, match="Daily budget"):
            wallet.request_x402("https://api.example.com", 0.60, "PaidAPI", "data")

        records = _read_audit(path)
        approved = [r for r in records if r["policy_result"] == "approved"]
        assert len(approved) == 1
        assert approved[0]["amount"] == 1.0
        assert approved[0]["settled_amount"] == 1.50

    def test_no_settled_amount_falls_back_to_requested(self):
        """Older facilitators that don't return an amount keep the old behavior."""
        gw = _SettlingX402Gateway(settled_atomic=None)
        wallet, path = _make_wallet(
            x402_gateway=gw,
            policy=SpendPolicy(daily_budget=2.0, max_transaction=5.0),
        )
        wallet.request_x402("https://api.example.com", 1.0, "PaidAPI", "data")

        records = _read_audit(path)
        approved = [r for r in records if r["policy_result"] == "approved"]
        assert len(approved) == 1
        assert approved[0]["amount"] == 1.0
        assert approved[0]["settled_amount"] is None

    def test_settled_amount_below_requested_still_commits_settled(self):
        """If facilitator settled *less* than requested, commit the smaller amount."""
        # Requested $1.00, settled $0.75 (750_000 atomic USDC).
        gw = _SettlingX402Gateway(settled_atomic="750000")
        wallet, path = _make_wallet(
            x402_gateway=gw,
            policy=SpendPolicy(daily_budget=1.0, max_transaction=5.0),
        )
        wallet.request_x402("https://api.example.com", 1.0, "PaidAPI", "data")

        # Daily budget was $1.00, we settled $0.75, so $0.25 remains.
        # A follow-up $0.20 must succeed.
        wallet.request_x402("https://api.example.com", 0.20, "PaidAPI", "more")

        records = _read_audit(path)
        approved = [r for r in records if r["policy_result"] == "approved"]
        assert len(approved) == 2
        assert approved[0]["settled_amount"] == 0.75


class TestX402GatewayHeaderParsing:
    """Direct test of X402Gateway.execute_async parsing the payment-response header."""

    def test_parses_settled_amount_from_header(self):
        # X402Gateway.execute_async imports x402.http.clients at call time,
        # so this test needs the optional [x402] extra installed. Skip when
        # it's not available (default CI matrix).
        pytest.importorskip("x402")

        import base64
        import json as _json
        from unittest.mock import AsyncMock, MagicMock

        gw = X402Gateway.__new__(X402Gateway)
        gw._client = None
        gw._payer = "0xPayer"

        settle = {
            "success": True,
            "transaction": "0xhash",
            "network": "eip155:8453",
            "amount": "2500000",  # 2_500_000 atomic USDC = $2.50 = 250 cents
        }
        header = base64.b64encode(_json.dumps(settle).encode()).decode()

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.headers = {
            "payment-response": header,
            "content-type": "application/json",
        }
        fake_response.text = "{}"
        fake_response.aread = AsyncMock(return_value=None)

        fake_client_ctx = MagicMock()
        fake_client_ctx.__aenter__ = AsyncMock(return_value=fake_client_ctx)
        fake_client_ctx.__aexit__ = AsyncMock(return_value=False)
        fake_client_ctx.request = AsyncMock(return_value=fake_response)

        import x402.http.clients as _clients

        original = _clients.x402HttpxClient
        _clients.x402HttpxClient = lambda *a, **kw: fake_client_ctx
        try:
            result = asyncio.run(
                gw.execute_async(100, "vendor", "memo", url="https://api.example.com")
            )
        finally:
            _clients.x402HttpxClient = original

        assert result.settled_amount_atomic == "2500000"
        assert result.settled_amount_cents == 250
        assert result.transaction_hash == "0xhash"


# ---------------------------------------------------------------------------
# Pre-signing cap: x402 requirement must not exceed the approved amount (#43)
# ---------------------------------------------------------------------------

BASE_SEPOLIA = "eip155:84532"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


def _requirement(amount_atomic: str, asset: str = BASE_SEPOLIA_USDC, network=BASE_SEPOLIA):
    from x402 import PaymentRequirements

    return PaymentRequirements(
        scheme="exact",
        network=network,
        asset=asset,
        amount=amount_atomic,
        pay_to=PAY_TO,
        max_timeout_seconds=60,
        extra={"name": "USDC", "version": "2"},
    )


class _X402Server:
    """httpx handler emulating an x402 endpoint that quotes fixed requirements."""

    def __init__(self, accepts):
        self.accepts = accepts
        self.requests = []

    def __call__(self, request):
        import httpx
        from x402 import PaymentRequired
        from x402.http.utils import encode_payment_required_header

        self.requests.append(request)
        if "payment-signature" not in request.headers:
            required = PaymentRequired(accepts=self.accepts)
            return httpx.Response(
                402, headers={"PAYMENT-REQUIRED": encode_payment_required_header(required)}
            )
        settle = {"success": True, "transaction": "0xpaid", "network": BASE_SEPOLIA}
        return httpx.Response(
            200,
            headers={
                "payment-response": base64.b64encode(json.dumps(settle).encode()).decode(),
                "content-type": "application/json",
            },
            text='{"ok": true}',
        )

    @property
    def signed_requests(self):
        return [r for r in self.requests if "payment-signature" in r.headers]


@pytest.fixture
def x402_env(monkeypatch):
    """Real X402Gateway (random key) wired to an in-memory x402 server.

    Returns a factory ``(accepts) -> (gateway, server, sign_calls)``;
    ``sign_calls`` records every requirement the scheme was asked to sign.
    """
    pytest.importorskip("x402")
    import httpx
    from eth_account import Account

    def factory(accepts):
        server = _X402Server(accepts)
        monkeypatch.setattr(
            httpx, "AsyncHTTPTransport", lambda *a, **kw: httpx.MockTransport(server)
        )
        gw = X402Gateway(evm_private_key=Account.create().key.hex())
        sign_calls = []
        for schemes in gw._client._schemes.values():
            for scheme in schemes.values():
                original = scheme.create_payment_payload

                def spy(requirements, *a, _orig=original, **kw):
                    sign_calls.append(requirements)
                    return _orig(requirements, *a, **kw)

                monkeypatch.setattr(scheme, "create_payment_payload", spy)
        return gw, server, sign_calls

    return factory



class TestX402ApprovedAmountCap:
    def test_requirement_above_approved_amount_is_not_signed(self, x402_env):
        # Approved $1.00, server quotes $1.50 (1_500_000 atomic USDC).
        gw, server, sign_calls = x402_env([_requirement("1500000")])

        with pytest.raises(RuntimeError, match="refused before signing"):
            gw.execute(100, "PaidAPI", "data", url="https://api.example.com/paid")

        assert sign_calls == []
        assert server.signed_requests == []
        assert len(server.requests) == 1

    def test_wallet_raises_and_does_not_commit_budget(self, x402_env):
        gw, server, sign_calls = x402_env([_requirement("1500000")])
        wallet, path = _make_wallet(
            x402_gateway=gw, policy=SpendPolicy(max_transaction=5.0, daily_budget=2.0)
        )

        with pytest.raises(GatewayError, match="refused before signing"):
            wallet.request_x402("https://api.example.com/paid", 1.0, "PaidAPI", "data")

        assert sign_calls == []
        assert server.signed_requests == []
        assert wallet.policy_engine._daily_spend == 0.0
        records = _read_audit(path)
        assert [r["policy_result"] for r in records] == ["denied"]
        assert records[0]["gateway_ref"] is None

    def test_async_path_also_enforces_cap(self, x402_env):
        gw, server, sign_calls = x402_env([_requirement("50000000")])  # $50
        wallet, _ = _make_wallet(x402_gateway=gw, policy=SpendPolicy(max_transaction=5.0))

        with pytest.raises(GatewayError, match="refused before signing"):
            asyncio.run(
                wallet.request_x402_async(
                    "https://api.example.com/paid", 1.0, "PaidAPI", "data"
                )
            )

        assert sign_calls == []
        assert server.signed_requests == []

    def test_requirement_equal_to_approved_amount_is_paid(self, x402_env):
        gw, server, sign_calls = x402_env([_requirement("1000000")])

        result = gw.execute(100, "PaidAPI", "data", url="https://api.example.com/paid")

        assert result.status_code == 200
        assert result.transaction_hash == "0xpaid"
        assert [r.amount for r in sign_calls] == ["1000000"]
        assert len(server.signed_requests) == 1

    def test_cheaper_acceptable_requirement_is_selected(self, x402_env):
        gw, server, sign_calls = x402_env(
            [_requirement("1500000"), _requirement("900000")]
        )

        gw.execute(100, "PaidAPI", "data", url="https://api.example.com/paid")

        assert [r.amount for r in sign_calls] == ["900000"]

    def test_unknown_asset_is_refused(self, x402_env):
        # Unregistered token: can't be valued in USD, so refuse even a tiny amount.
        unknown = "0x000000000000000000000000000000000000dEaD"
        gw, server, sign_calls = x402_env([_requirement("1", asset=unknown)])

        with pytest.raises(RuntimeError, match="refused before signing"):
            gw.execute(100, "PaidAPI", "data", url="https://api.example.com/paid")

        assert sign_calls == []
        assert server.signed_requests == []

    def test_cap_does_not_leak_between_requests(self, x402_env):
        gw, server, sign_calls = x402_env([_requirement("750000")])

        gw.execute(100, "PaidAPI", "data", url="https://api.example.com/paid")
        with pytest.raises(RuntimeError, match="refused before signing"):
            gw.execute(50, "PaidAPI", "data", url="https://api.example.com/paid")

        assert [r.amount for r in sign_calls] == ["750000"]

    def test_hook_aborts_when_selected_requirement_over_cap(self):
        """Defense in depth: the before-payment hook rejects even if selection
        somehow yields an over-cap requirement."""
        pytest.importorskip("x402")
        from types import SimpleNamespace

        from x402 import AbortResult

        from paygraph.gateways.x402 import _approved_cents

        ctx = SimpleNamespace(selected_requirements=_requirement("1500000"))
        token = _approved_cents.set(100)
        try:
            assert isinstance(X402Gateway._abort_if_over_cap(ctx), AbortResult)
        finally:
            _approved_cents.reset(token)
        # No approved amount in scope → fail closed.
        assert isinstance(X402Gateway._abort_if_over_cap(ctx), AbortResult)

    def test_cap_respects_asset_decimals(self):
        pytest.importorskip("x402")
        from paygraph.gateways.x402 import _requirement_within_cap

        # MegaUSD on MegaETH has 18 decimals: $1.00 == 10**18 atomic units.
        mega = "0xFAfDdbb3FC7688494971a79cc65DCa3EF82079E7"
        assert _requirement_within_cap(
            _requirement(str(10**18), asset=mega, network="eip155:4326"), 100
        )
        assert not _requirement_within_cap(
            _requirement(str(10**18 + 1), asset=mega, network="eip155:4326"), 100
        )
