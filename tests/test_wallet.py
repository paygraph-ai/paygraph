import json
import tempfile

import pytest

from paygraph.exceptions import GatewayError, PolicyViolationError, SpendDeniedError
from paygraph.gateways.base import BaseGateway, VirtualCard
from paygraph.gateways.mock import MockGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet


def _make_wallet(gateway=None, policy=None, **kwargs) -> tuple[AgentWallet, str]:
    """Create a wallet with a temp audit file. Returns (wallet, audit_path)."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return (
        AgentWallet(
            gateway=gateway or MockGateway(auto_approve=True),
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


class TestApprovedSpend:
    def test_returns_card_string(self):
        wallet, _ = _make_wallet()
        result = wallet.request_spend(4.20, "Anthropic", "need credits")
        assert "Card approved" in result
        assert "4111111111111111" in result
        assert "123" in result
        assert "12/28" in result

    def test_audit_records_approval(self):
        wallet, path = _make_wallet()
        wallet.request_spend(4.20, "Anthropic", "need credits")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "approved"
        assert records[0]["amount"] == 4.20
        assert records[0]["vendor"] == "Anthropic"
        assert records[0]["gateway_ref"] is not None
        assert records[0]["gateway_type"] == "mock"

    def test_returns_spt_token_string_for_mpp_gateway(self):
        class MppGateway(BaseGateway):
            def execute_spend(self, amount_cents, vendor, memo):
                return VirtualCard(
                    pan="SPT_NO_PAN",
                    cvv="N/A",
                    expiry="--/--",
                    spend_limit_cents=amount_cents,
                    gateway_ref="spt_test_abc123",
                    gateway_type="stripe_mpp_test",
                )

            def revoke(self, gateway_ref):
                return True

        wallet, _ = _make_wallet(gateway=MppGateway())
        result = wallet.request_spend(4.20, "Anthropic", "need credits")
        assert result == "SPT approved. Token: spt_test_abc123 (spend limit: $4.20)"


class TestPolicyDenial:
    def test_raises_policy_violation(self):
        wallet, _ = _make_wallet(policy=SpendPolicy(max_transaction=5.0))
        with pytest.raises(PolicyViolationError, match="exceeds limit"):
            wallet.request_spend(100.0, "vendor", "reason")

    def test_audit_records_denial(self):
        wallet, path = _make_wallet(policy=SpendPolicy(max_transaction=5.0))
        with pytest.raises(PolicyViolationError):
            wallet.request_spend(100.0, "vendor", "reason")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "denied"
        assert records[0]["gateway_ref"] is None


class TestHumanDenial:
    def test_raises_spend_denied(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        wallet, _ = _make_wallet(gateway=MockGateway(auto_approve=False))
        with pytest.raises(SpendDeniedError):
            wallet.request_spend(4.20, "Anthropic", "need credits")

    def test_audit_records_human_denial(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        wallet, path = _make_wallet(gateway=MockGateway(auto_approve=False))
        with pytest.raises(SpendDeniedError):
            wallet.request_spend(4.20, "Anthropic", "need credits")
        records = _read_audit(path)
        assert len(records) == 1
        assert records[0]["policy_result"] == "denied"
        assert "Human denied" in records[0]["denial_reason"]


class TestGatewayFailure:
    def test_raises_gateway_error(self):
        class BrokenGateway(BaseGateway):
            def execute_spend(self, amount_cents, vendor, memo):
                raise RuntimeError("connection refused")

            def revoke(self, gateway_ref):
                return False

        wallet, _ = _make_wallet(gateway=BrokenGateway())
        with pytest.raises(GatewayError, match="connection refused"):
            wallet.request_spend(4.20, "vendor", "reason")

    def test_audit_records_gateway_failure(self):
        class BrokenGateway(BaseGateway):
            def execute_spend(self, amount_cents, vendor, memo):
                raise RuntimeError("timeout")

            def revoke(self, gateway_ref):
                return False

        wallet, path = _make_wallet(gateway=BrokenGateway())
        with pytest.raises(GatewayError):
            wallet.request_spend(4.20, "vendor", "reason")
        records = _read_audit(path)
        assert records[0]["policy_result"] == "denied"
        assert "Gateway error" in records[0]["denial_reason"]


class TestGatewayFailureDoesNotConsumeBudget:
    def test_daily_budget_not_decremented_on_gateway_error(self):
        """A gateway failure must not consume the daily budget (issue #9)."""

        class BrokenGateway(BaseGateway):
            def execute_spend(self, amount_cents, vendor, memo):
                raise RuntimeError("network timeout")

            def revoke(self, gateway_ref):
                return False

        policy = SpendPolicy(max_transaction=50.0, daily_budget=100.0)
        wallet, _ = _make_wallet(gateway=BrokenGateway(), policy=policy)

        with pytest.raises(GatewayError):
            wallet.request_spend(40.0, "vendor", "reason")

        # Budget must still be fully available — the failed spend should not count
        assert wallet.policy_engine._daily_spend == 0.0

        wallet.gateway = MockGateway(auto_approve=True)
        result = wallet.request_spend(40.0, "vendor", "reason")
        assert "Card approved" in result


class TestAgentId:
    def test_agent_id_in_audit(self):
        wallet, path = _make_wallet(agent_id="agent-007")
        wallet.request_spend(5.0, "vendor", "reason")
        records = _read_audit(path)
        assert records[0]["agent_id"] == "agent-007"


class TestNoPanInAudit:
    def test_no_raw_pan(self):
        wallet, path = _make_wallet()
        wallet.request_spend(5.0, "vendor", "reason")
        with open(path) as f:
            content = f.read()
        assert "4111111111111111" not in content
