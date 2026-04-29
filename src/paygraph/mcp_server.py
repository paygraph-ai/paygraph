"""MCP server exposing PayGraph spend governance tools.

This module implements an MCP (Model Context Protocol) server that allows
AI agents to request virtual cards and check budget status through the
standardized MCP interface.

Example:
    Run the server with stdio transport::

        paygraph mcp serve

    Or with HTTP transport::

        paygraph mcp serve --transport http --port 8080
"""

from mcp.server.fastmcp import FastMCP

from paygraph.exceptions import GatewayError, PolicyViolationError
from paygraph.gateways.mock import MockGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet


def create_mcp_server(
    wallet: AgentWallet | None = None,
    name: str = "paygraph",
) -> FastMCP:
    """Create an MCP server with PayGraph tools.

    Args:
        wallet: Pre-configured AgentWallet. If None, creates a default
            wallet with MockGateway and standard policy.
        name: Server name for MCP identification.

    Returns:
        Configured FastMCP server instance.
    """
    mcp = FastMCP(name)

    if wallet is None:
        wallet = AgentWallet(
            gateway=MockGateway(auto_approve=True),
            policy=SpendPolicy(),
        )

    @mcp.tool()
    def mint_virtual_card(
        amount: float,
        vendor: str,
        justification: str,
    ) -> str:
        """Request a virtual card for a purchase.

        Evaluates the spend against the configured policy, then mints
        a virtual card if approved.

        Args:
            amount: Dollar amount to spend (e.g. 4.20 for $4.20).
            vendor: Name of the vendor or service.
            justification: Explanation of why this purchase is necessary.

        Returns:
            Card details (PAN, CVV, expiry) or error message.
        """
        try:
            result = wallet.request_spend(amount, vendor, justification)
            return result
        except PolicyViolationError as e:
            return f"Policy denied: {e}"
        except GatewayError as e:
            return f"Gateway error: {e}"

    @mcp.tool()
    def check_budget_status() -> str:
        """Check remaining budget across all time periods.

        Returns:
            Summary of daily spend and remaining budget.
        """
        engine = wallet.policy_engine
        policy = engine.policy
        daily_spent = engine._daily_spend
        daily_remaining = policy.daily_budget - daily_spent

        lines = [
            f"Daily: ${daily_spent:.2f} spent / ${policy.daily_budget:.2f} limit "
            f"(${daily_remaining:.2f} remaining)",
            f"Max transaction: ${policy.max_transaction:.2f}",
        ]

        if policy.hourly_budget is not None:
            lines.append(f"Hourly limit: ${policy.hourly_budget:.2f}")
        if policy.weekly_budget is not None:
            lines.append(f"Weekly limit: ${policy.weekly_budget:.2f}")
        if policy.monthly_budget is not None:
            lines.append(f"Monthly limit: ${policy.monthly_budget:.2f}")

        return "\n".join(lines)

    @mcp.tool()
    def get_policy_info() -> str:
        """Get current spend policy configuration.

        Returns:
            Policy settings including limits and restrictions.
        """
        policy = wallet.policy_engine.policy
        lines = [
            f"Max transaction: ${policy.max_transaction:.2f}",
            f"Daily budget: ${policy.daily_budget:.2f}",
            f"Justification required: {policy.require_justification}",
        ]

        if policy.allowed_vendors:
            lines.append(f"Allowed vendors: {', '.join(policy.allowed_vendors)}")
        if policy.blocked_vendors:
            lines.append(f"Blocked vendors: {', '.join(policy.blocked_vendors)}")

        return "\n".join(lines)

    return mcp
