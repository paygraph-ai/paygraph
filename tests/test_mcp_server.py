"""Tests for MCP server functionality."""

import tempfile

import pytest

from paygraph.gateways.mock import MockGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet

mcp_module = pytest.importorskip("mcp", reason="MCP extras not installed")


def _make_server(policy=None, gateway=None):
    """Create an MCP server with a temp audit file. Returns (server, wallet)."""
    from paygraph.mcp_server import create_mcp_server

    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    wallet = AgentWallet(
        gateway=gateway or MockGateway(auto_approve=True),
        policy=policy or SpendPolicy(),
        log_path=f.name,
        verbose=False,
    )
    server = create_mcp_server(wallet=wallet)
    return server, wallet


class TestMCPServerCreation:
    """Test MCP server initialization."""

    def test_create_server_with_default_wallet(self):
        from paygraph.mcp_server import create_mcp_server

        server = create_mcp_server()
        assert server is not None
        assert server.name == "paygraph"

    def test_create_server_with_custom_name(self):
        from paygraph.mcp_server import create_mcp_server

        server = create_mcp_server(name="custom-paygraph")
        assert server.name == "custom-paygraph"

    def test_create_server_with_custom_wallet(self):
        from paygraph.mcp_server import create_mcp_server

        wallet = AgentWallet(
            gateway=MockGateway(auto_approve=True),
            policy=SpendPolicy(max_transaction=100.0),
            verbose=False,
        )
        server = create_mcp_server(wallet=wallet)
        assert server is not None


class TestMintVirtualCardTool:
    """Test mint_virtual_card MCP tool."""

    def test_successful_mint(self):
        server, wallet = _make_server(
            policy=SpendPolicy(max_transaction=50.0, daily_budget=200.0)
        )
        result = wallet.request_spend(4.20, "Anthropic", "need credits")
        assert "Card approved" in result

    def test_policy_denied_exceeds_cap(self):
        server, wallet = _make_server(policy=SpendPolicy(max_transaction=10.0))
        with pytest.raises(Exception, match="exceeds limit"):
            wallet.request_spend(50.0, "vendor", "reason")

    def test_policy_denied_blocked_vendor(self):
        server, wallet = _make_server(policy=SpendPolicy(blocked_vendors=["doordash"]))
        with pytest.raises(Exception, match="blocked"):
            wallet.request_spend(5.0, "DoorDash", "lunch")


class TestBudgetStatus:
    """Test budget status reporting via wallet internals."""

    def test_initial_budget_is_full(self):
        _, wallet = _make_server(policy=SpendPolicy(daily_budget=200.0))
        engine = wallet.policy_engine
        assert engine._daily_spend == 0.0
        assert engine.policy.daily_budget == 200.0

    def test_budget_decreases_after_spend(self):
        _, wallet = _make_server(
            policy=SpendPolicy(max_transaction=100.0, daily_budget=200.0)
        )
        wallet.request_spend(30.0, "vendor", "reason")
        engine = wallet.policy_engine
        assert engine._daily_spend == 30.0

    def test_time_based_budget_fields(self):
        _, wallet = _make_server(
            policy=SpendPolicy(
                hourly_budget=50.0,
                weekly_budget=300.0,
                monthly_budget=1000.0,
            )
        )
        policy = wallet.policy_engine.policy
        assert policy.hourly_budget == 50.0
        assert policy.weekly_budget == 300.0
        assert policy.monthly_budget == 1000.0


class TestPolicyInfo:
    """Test policy info reporting."""

    def test_default_policy_values(self):
        _, wallet = _make_server()
        policy = wallet.policy_engine.policy
        assert policy.max_transaction == 50.0
        assert policy.daily_budget == 200.0
        assert policy.require_justification is True

    def test_custom_policy_values(self):
        _, wallet = _make_server(
            policy=SpendPolicy(
                max_transaction=100.0,
                daily_budget=500.0,
                blocked_vendors=["doordash", "ubereats"],
            )
        )
        policy = wallet.policy_engine.policy
        assert policy.max_transaction == 100.0
        assert policy.daily_budget == 500.0
        assert policy.blocked_vendors == ["doordash", "ubereats"]

    def test_allowed_vendors_policy(self):
        _, wallet = _make_server(
            policy=SpendPolicy(allowed_vendors=["anthropic", "openai"])
        )
        policy = wallet.policy_engine.policy
        assert policy.allowed_vendors == ["anthropic", "openai"]
