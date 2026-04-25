"""Tests for PayGraph MCP server wiring and environment bootstrap."""

import asyncio
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Import mcp.types at module level so its Pydantic generic submodels register
# before any other test in the suite patches sys.modules. The whole module is
# skipped when the optional `mcp` extra is not installed.
mcp_types = pytest.importorskip("mcp.types")

from paygraph.exceptions import HumanApprovalRequired  # noqa: E402
from paygraph.gateways.mock import MockGateway  # noqa: E402
from paygraph.gateways.mock_x402 import MockX402Gateway  # noqa: E402
from paygraph.gateways.stripe import StripeCardGateway  # noqa: E402
from paygraph.gateways.stripe_mpp import StripeMPPGateway  # noqa: E402
from paygraph.mcp_server import (  # noqa: E402
    _build_wallet_from_env,
    build_server,
    main,
)
from paygraph.policy import SpendPolicy  # noqa: E402
from paygraph.wallet import AgentWallet  # noqa: E402


def _make_wallet(policy: SpendPolicy | None = None) -> AgentWallet:
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    f.close()
    return AgentWallet(
        gateways={
            "default": MockGateway(auto_approve=True),
            "x402": MockX402Gateway(auto_approve=True, response_body='{"ok": true}'),
        },
        policy=policy or SpendPolicy(),
        log_path=f.name,
        verbose=False,
    )


class TestBuildServer:
    def test_server_has_both_tools_with_schema(self):

        server = build_server(_make_wallet())
        handler = server.request_handlers[mcp_types.ListToolsRequest]

        result = asyncio.run(
            handler(
                mcp_types.ListToolsRequest(
                    method="tools/list",
                    params=mcp_types.PaginatedRequestParams(),
                )
            )
        )

        tools = {tool.name: tool for tool in result.root.tools}
        assert "paygraph_request_spend" in tools
        assert "paygraph_request_x402" in tools
        assert tools["paygraph_request_spend"].inputSchema["type"] == "object"
        assert tools["paygraph_request_x402"].inputSchema["type"] == "object"


class TestSpendToolViaServer:
    def test_success_returns_wallet_reply(self):

        server = build_server(_make_wallet())
        handler = server.request_handlers[mcp_types.CallToolRequest]

        result = asyncio.run(
            handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="paygraph_request_spend",
                        arguments={
                            "amount": 4.2,
                            "vendor": "Anthropic",
                            "justification": "Need credits",
                        },
                    ),
                )
            )
        )

        assert result.root.isError is False
        assert "Card approved" in result.root.content[0].text

    def test_policy_violation_returns_error_with_reason(self):

        wallet = _make_wallet(policy=SpendPolicy(max_transaction=1.0))
        server = build_server(wallet)
        handler = server.request_handlers[mcp_types.CallToolRequest]

        result = asyncio.run(
            handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="paygraph_request_spend",
                        arguments={
                            "amount": 4.2,
                            "vendor": "Anthropic",
                            "justification": "Need credits",
                        },
                    ),
                )
            )
        )

        assert result.root.isError is True
        assert "exceeds limit" in result.root.content[0].text
        assert result.root.structuredContent["error"] == "policy_violation"

    def test_human_approval_required_is_structured_error(self):

        wallet = _make_wallet()
        wallet.request_spend = MagicMock(
            side_effect=HumanApprovalRequired(
                request_id="req_123",
                amount=42.0,
                vendor="Vendor",
                gateway_name="default",
            )
        )
        server = build_server(wallet)
        handler = server.request_handlers[mcp_types.CallToolRequest]

        result = asyncio.run(
            handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="paygraph_request_spend",
                        arguments={
                            "amount": 42.0,
                            "vendor": "Vendor",
                            "justification": "Need approval",
                        },
                    ),
                )
            )
        )

        assert result.root.isError is True
        assert result.root.structuredContent["error"] == "human_approval_required"
        assert result.root.structuredContent["data"]["request_id"] == "req_123"


class TestX402ToolViaServer:
    def test_success_returns_wallet_reply(self):

        server = build_server(_make_wallet())
        handler = server.request_handlers[mcp_types.CallToolRequest]

        result = asyncio.run(
            handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="paygraph_request_x402",
                        arguments={
                            "url": "https://api.example.com/data",
                            "amount": 0.25,
                            "vendor": "ExampleAPI",
                            "justification": "Need data",
                            "method": "GET",
                        },
                    ),
                )
            )
        )

        assert result.root.isError is False
        assert result.root.content[0].text == '{"ok": true}'

    def test_validation_error_returns_tool_error(self):

        server = build_server(_make_wallet())
        handler = server.request_handlers[mcp_types.CallToolRequest]

        result = asyncio.run(
            handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="paygraph_request_x402",
                        arguments={
                            "url": "https://api.example.com/data",
                            "vendor": "ExampleAPI",
                            "justification": "Need data",
                        },
                    ),
                )
            )
        )

        assert result.root.isError is True
        assert "Input validation error" in result.root.content[0].text


class TestEnvBootstrap:
    def test_mock_gateway_bootstrap(self):
        with patch.dict(
            "os.environ",
            {
                "PAYGRAPH_GATEWAY": "mock",
                "PAYGRAPH_DAILY_BUDGET": "100",
                "PAYGRAPH_MAX_TRANSACTION": "25",
                "PAYGRAPH_AUDIT_LOG_PATH": "audit.log",
            },
            clear=True,
        ):
            wallet = _build_wallet_from_env()

        assert isinstance(wallet._resolve_gateway("default"), MockGateway)
        assert isinstance(wallet._resolve_gateway("x402"), MockX402Gateway)
        assert wallet.policy_engine.policy.daily_budget == 100.0
        assert wallet.policy_engine.policy.max_transaction == 25.0

    def test_stripe_gateway_bootstrap(self):
        with patch.dict(
            "os.environ",
            {
                "PAYGRAPH_GATEWAY": "stripe",
                "PAYGRAPH_API_KEY": "sk_test_123",
            },
            clear=True,
        ):
            wallet = _build_wallet_from_env()

        assert isinstance(wallet._resolve_gateway("default"), StripeCardGateway)

    def test_stripe_mpp_gateway_bootstrap(self):
        with patch.dict(
            "os.environ",
            {
                "PAYGRAPH_GATEWAY": "stripe_mpp",
                "PAYGRAPH_API_KEY": "sk_test_123",
                "STRIPE_MPP_PAYMENT_METHOD": "pm_123",
                "STRIPE_MPP_GRANTEE": "profile_123",
            },
            clear=True,
        ):
            wallet = _build_wallet_from_env()

        assert isinstance(wallet._resolve_gateway("default"), StripeMPPGateway)


class TestImportErrorMessage:
    def test_main_raises_helpful_import_error_without_mcp(self):
        with patch.dict(
            "sys.modules",
            {
                "mcp": None,
                "mcp.types": None,
                "mcp.server": None,
                "mcp.server.models": None,
                "mcp.server.stdio": None,
            },
        ):
            with pytest.raises(ImportError, match="pip install paygraph\\[mcp\\]"):
                main()
