"""MCP server exposing PayGraph spend and x402 tools over stdio."""

from __future__ import annotations

import asyncio
import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from pydantic import BaseModel, Field

from paygraph.exceptions import (
    GatewayError,
    HumanApprovalRequired,
    PolicyViolationError,
    SpendDeniedError,
)
from paygraph.gateways.mock import MockGateway
from paygraph.gateways.mock_x402 import MockX402Gateway
from paygraph.gateways.stripe import StripeCardGateway
from paygraph.gateways.stripe_mpp import StripeMPPGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet

_MCP_IMPORT_ERROR = (
    "MCP integration requires mcp. Install it with: pip install paygraph[mcp]"
)


class SpendRequest(BaseModel):
    amount: float = Field(
        description="The exact dollar amount to spend (e.g. 4.20 for $4.20)"
    )
    vendor: str = Field(
        description="The name of the vendor or service to pay (e.g. 'Anthropic API')"
    )
    justification: str = Field(
        description=(
            "A detailed explanation of why this purchase is necessary to complete "
            "your task"
        )
    )


class X402Request(BaseModel):
    url: str = Field(description="The x402-enabled API endpoint URL")
    amount: float = Field(description="Dollar amount for the request")
    vendor: str = Field(description="Name of the service/vendor")
    justification: str = Field(description="Why this API call is needed")
    method: str = Field(default="GET", description="HTTP method")
    headers: dict[str, str] | None = Field(
        default=None,
        description="Optional HTTP headers",
    )
    body: str | None = Field(default=None, description="Optional request body")


def _load_mcp() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import mcp.types as types
        from mcp.server import NotificationOptions, Server
        from mcp.server.models import InitializationOptions
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - covered in test_import_error
        raise ImportError(_MCP_IMPORT_ERROR) from exc

    return types, Server, NotificationOptions, InitializationOptions, stdio_server


def _build_wallet_from_env() -> AgentWallet:
    gateway_name = os.environ.get("PAYGRAPH_GATEWAY", "mock").lower()

    daily_budget = float(os.environ.get("PAYGRAPH_DAILY_BUDGET", "200"))
    max_transaction = float(os.environ.get("PAYGRAPH_MAX_TRANSACTION", "50"))
    log_path = os.environ.get("PAYGRAPH_AUDIT_LOG_PATH", "paygraph_audit.jsonl")

    policy = SpendPolicy(
        daily_budget=daily_budget,
        max_transaction=max_transaction,
    )

    if gateway_name == "mock":
        default_gateway = MockGateway(auto_approve=True)
    elif gateway_name == "stripe":
        api_key = os.environ.get("PAYGRAPH_API_KEY")
        if not api_key:
            raise ValueError("PAYGRAPH_API_KEY is required when PAYGRAPH_GATEWAY=stripe")
        default_gateway = StripeCardGateway(api_key=api_key)
    elif gateway_name == "stripe_mpp":
        api_key = os.environ.get("PAYGRAPH_API_KEY")
        if not api_key:
            raise ValueError(
                "PAYGRAPH_API_KEY is required when PAYGRAPH_GATEWAY=stripe_mpp"
            )

        payment_method = os.environ.get("STRIPE_MPP_PAYMENT_METHOD")
        if not payment_method:
            raise ValueError(
                "STRIPE_MPP_PAYMENT_METHOD is required when "
                "PAYGRAPH_GATEWAY=stripe_mpp"
            )

        grantee = os.environ.get("STRIPE_MPP_GRANTEE")
        if not grantee:
            raise ValueError(
                "STRIPE_MPP_GRANTEE is required when PAYGRAPH_GATEWAY=stripe_mpp"
            )

        default_gateway = StripeMPPGateway(
            api_key=api_key,
            payment_method=payment_method,
            grantee=grantee,
        )
    else:
        raise ValueError(
            "PAYGRAPH_GATEWAY must be one of: mock, stripe, stripe_mpp"
        )

    return AgentWallet(
        gateways={
            "default": default_gateway,
            "x402": MockX402Gateway(auto_approve=True),
        },
        policy=policy,
        log_path=log_path,
        verbose=False,
    )


def _error_result(types: Any, code: str, message: str, **data: Any) -> Any:
    structured = {"error": code, "message": message}
    if data:
        structured["data"] = data
    return types.CallToolResult(
        isError=True,
        content=[types.TextContent(type="text", text=message)],
        structuredContent=structured,
    )


def build_server(wallet: AgentWallet):
    """Create an MCP server exposing spend and x402 tools."""
    types, Server, _, _, _ = _load_mcp()

    server = Server("paygraph")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name="paygraph_request_spend",
                description="Request a policy-checked spend via PayGraph.",
                inputSchema=SpendRequest.model_json_schema(),
            ),
            types.Tool(
                name="paygraph_request_x402",
                description="Request a policy-checked x402 payment via PayGraph.",
                inputSchema=X402Request.model_json_schema(),
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        try:
            if name == "paygraph_request_spend":
                request = SpendRequest.model_validate(arguments)
                response = wallet.request_spend(
                    request.amount,
                    request.vendor,
                    request.justification,
                )
                return [types.TextContent(type="text", text=response)]

            if name == "paygraph_request_x402":
                request = X402Request.model_validate(arguments)
                response = await wallet.request_x402_async(
                    url=request.url,
                    amount=request.amount,
                    vendor=request.vendor,
                    justification=request.justification,
                    method=request.method,
                    headers=request.headers,
                    body=request.body,
                )
                return [types.TextContent(type="text", text=response)]

            raise ValueError(f"Unknown tool: {name}")
        except PolicyViolationError as exc:
            return _error_result(types, "policy_violation", str(exc))
        except HumanApprovalRequired as exc:
            return _error_result(
                types,
                "human_approval_required",
                str(exc),
                request_id=exc.request_id,
                amount=exc.amount,
                vendor=exc.vendor,
                gateway_name=exc.gateway_name,
            )
        except (GatewayError, SpendDeniedError, ValueError) as exc:
            return _error_result(types, "gateway_error", str(exc))

    return server


async def _run_server(server: Any) -> None:
    types, _, NotificationOptions, InitializationOptions, stdio_server = _load_mcp()

    try:
        server_version = version("paygraph")
    except PackageNotFoundError:
        server_version = "0.0.0"

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="paygraph",
                server_version=server_version,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    """Run the PayGraph MCP server over stdio."""
    _load_mcp()
    wallet = _build_wallet_from_env()
    server = build_server(wallet)
    asyncio.run(_run_server(server))
