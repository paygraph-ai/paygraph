"""Tests proving that card + x402 gateways share a single PolicyEngine budget."""

import json
import tempfile

import pytest

from paygraph.exceptions import PolicyViolationError
from paygraph.gateways.mock import MockGateway
from paygraph.gateways.mock_x402 import MockX402Gateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet


def _make_wallet(gateways, policy=None, **kwargs) -> tuple[AgentWallet, str]:
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return (
        AgentWallet(
            gateways=gateways,
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


class TestCrossGatewayBudgetTracking:
    def test_card_and_x402_share_daily_budget(self):
        """Card + x402 spends draw from the same daily budget."""
        wallet, _ = _make_wallet(
            gateways={
                "default": MockGateway(auto_approve=True),
                "x402": MockX402Gateway(auto_approve=True),
            },
            policy=SpendPolicy(daily_budget=10.0, max_transaction=50.0),
        )
        wallet.request_spend(6.0, "vendor", "reason")  # $6 via card
        wallet.request_x402(
            "https://api.example.com", 3.0, "vendor", "reason"
        )  # $3 via x402
        with pytest.raises(PolicyViolationError, match="Daily budget"):
            wallet.request_spend(5.0, "vendor", "reason")  # $5 should fail

    def test_x402_then_card_share_budget(self):
        """x402 spend first, then card — budget is shared."""
        wallet, _ = _make_wallet(
            gateways={
                "default": MockGateway(auto_approve=True),
                "x402": MockX402Gateway(auto_approve=True),
            },
            policy=SpendPolicy(daily_budget=10.0, max_transaction=50.0),
        )
        wallet.request_x402(
            "https://api.example.com", 8.0, "vendor", "reason"
        )  # $8 via x402
        with pytest.raises(PolicyViolationError, match="Daily budget"):
            wallet.request_spend(5.0, "vendor", "reason")  # $5 should fail

    def test_multiple_gateways_all_share_budget(self):
        """Three gateways all share the same PolicyEngine."""
        wallet, _ = _make_wallet(
            gateways={
                "card1": MockGateway(auto_approve=True),
                "card2": MockGateway(auto_approve=True),
                "x402": MockX402Gateway(auto_approve=True),
            },
            policy=SpendPolicy(daily_budget=15.0, max_transaction=50.0),
        )
        wallet.request_spend(5.0, "v", "r", gateway="card1")
        wallet.request_spend(5.0, "v", "r", gateway="card2")
        wallet.request_x402("https://api.example.com", 4.0, "v", "r")
        # Total: $14 of $15 budget used
        with pytest.raises(PolicyViolationError, match="Daily budget"):
            wallet.request_spend(5.0, "v", "r", gateway="card1")  # $5 over budget

    def test_audit_records_different_gateway_types(self):
        """Audit log captures correct gateway_type for each gateway."""
        wallet, path = _make_wallet(
            gateways={
                "default": MockGateway(auto_approve=True),
                "x402": MockX402Gateway(auto_approve=True),
            },
            policy=SpendPolicy(daily_budget=100.0, max_transaction=50.0),
        )
        wallet.request_spend(5.0, "CardVendor", "card reason")
        wallet.request_x402("https://api.example.com", 3.0, "X402Vendor", "x402 reason")

        records = _read_audit(path)
        assert len(records) == 2
        assert records[0]["gateway_type"] == "mock"
        assert records[0]["vendor"] == "CardVendor"
        assert records[1]["gateway_type"] == "x402"
        assert records[1]["vendor"] == "X402Vendor"
