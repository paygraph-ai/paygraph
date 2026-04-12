from functools import cached_property

from paygraph.audit import AuditLogger, AuditRecord
from paygraph.exceptions import GatewayError, PolicyViolationError, SpendDeniedError
from paygraph.gateways.mock import MockGateway
from paygraph.policy import PolicyEngine, SpendPolicy


class AgentWallet:
    """Main entry point for PayGraph spend governance.

    Orchestrates policy checks, gateway calls, and audit logging for both
    virtual card and x402 payment flows.

    Example:
        ```python
        from paygraph import AgentWallet, SpendPolicy

        wallet = AgentWallet(
            policy=SpendPolicy(max_transaction=25.0, daily_budget=100.0),
        )
        result = wallet.request_spend(4.20, "Anthropic API", "Need tokens")
        ```
    """

    def __init__(
        self,
        gateway=None,
        x402_gateway=None,
        policy: SpendPolicy | None = None,
        agent_id: str = "default",
        log_path: str = "paygraph_audit.jsonl",
        verbose: bool = True,
        animate: bool = False,
    ) -> None:
        """Initialize the wallet with a gateway, policy, and audit settings.

        Args:
            gateway: Card gateway implementing ``BaseGateway``. Defaults to
                ``MockGateway()`` which prompts for human approval.
            x402_gateway: Optional x402 gateway (``X402Gateway`` or
                ``MockX402Gateway``) for HTTP 402 payments.
            policy: Spend policy rules. Defaults to ``SpendPolicy()`` with
                $50 max transaction and $200 daily budget.
            agent_id: Identifier for this agent in audit logs.
            log_path: File path for the JSONL audit log.
            verbose: If True, print policy check results to stdout.
            animate: If True, add a short delay between policy checks
                for visual effect in demos.
        """
        self.gateway = gateway or MockGateway()
        self.x402_gateway = x402_gateway
        self.policy_engine = PolicyEngine(policy or SpendPolicy())
        self.agent_id = agent_id
        self._audit = AuditLogger(log_path=log_path, verbose=verbose, animate=animate)

    def request_spend(self, amount: float, vendor: str, justification: str) -> str:
        """Request a policy-checked virtual card spend.

        Evaluates the spend against the configured policy, then calls the
        card gateway to mint a virtual card if approved.

        Args:
            amount: Dollar amount to spend (e.g. 4.20 for $4.20).
            vendor: Name of the vendor or service.
            justification: Explanation of why this purchase is necessary.

        Returns:
            For most gateways, a string with card details (PAN, CVV, expiry).
            For ``stripe_mpp_*`` gateways, a string with the SPT id and spend limit.

        Raises:
            PolicyViolationError: If the policy engine denies the request.
            SpendDeniedError: If a human denies the request (MockGateway).
            GatewayError: If the gateway API call fails.
        """
        # Print header and run policy engine with live check output
        on_check = (
            self._audit.start_request(amount, vendor) if self._audit.verbose else None
        )
        result = self.policy_engine.evaluate(
            amount, vendor, justification, on_check=on_check
        )

        if not result.approved:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason=result.denial_reason,
                    checks_passed=result.checks_passed,
                )
            )
            raise PolicyViolationError(result.denial_reason)

        # Mint card
        amount_cents = int(round(amount * 100))
        try:
            card = self.gateway.execute_spend(amount_cents, vendor, justification)
        except SpendDeniedError:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason="Human denied the spend request",
                    checks_passed=result.checks_passed,
                )
            )
            raise
        except Exception as e:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason=f"Gateway error: {e}",
                    checks_passed=result.checks_passed,
                )
            )
            raise GatewayError(str(e)) from e

        # Log approval
        self._audit.log(
            AuditRecord.now(
                agent_id=self.agent_id,
                amount=amount,
                vendor=vendor,
                justification=justification,
                policy_result="approved",
                checks_passed=result.checks_passed,
                gateway_ref=card.gateway_ref,
                gateway_type=card.gateway_type,
            )
        )

        if card.gateway_type.startswith("stripe_mpp"):
            return (
                f"SPT approved. Token: {card.gateway_ref} (spend limit: ${amount:.2f})"
            )

        return f"Card approved. PAN: {card.pan}, CVV: {card.cvv}, Expiry: {card.expiry}"

    @cached_property
    def spend_tool(self):
        """LangChain-compatible tool for virtual card spends.

        Returns a ``@tool``-decorated function usable in LangGraph agents.
        Requires ``langchain-core``: install with ``pip install paygraph[langgraph]``.
        """
        return self._build_spend_tool()

    def _build_spend_tool(self):
        try:
            from langchain_core.tools import tool
        except ImportError:
            raise ImportError(
                "LangGraph integration requires langchain-core. "
                "Install it with: pip install paygraph[langgraph]"
            )

        from pydantic import BaseModel, Field

        class SpendRequest(BaseModel):
            amount: float = Field(
                description="The exact dollar amount to spend (e.g. 4.20 for $4.20)"
            )
            vendor: str = Field(
                description="The name of the vendor or service to pay (e.g. 'Anthropic API')"
            )
            justification: str = Field(
                description="A detailed explanation of why this purchase is necessary to complete your task"
            )

        wallet = self

        @tool("mint_virtual_card", args_schema=SpendRequest)
        def mint_virtual_card(amount: float, vendor: str, justification: str) -> str:
            """Use this tool when you need to spend money to complete your task. You must provide the exact dollar amount, the vendor name, and a detailed justification explaining why this purchase is necessary."""
            try:
                return wallet.request_spend(amount, vendor, justification)
            except (PolicyViolationError, SpendDeniedError, GatewayError) as e:
                return f"Spend denied: {e}"

        return mint_virtual_card

    def request_x402(
        self,
        url: str,
        amount: float,
        vendor: str,
        justification: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> str:
        """Make a policy-checked x402 payment to a paid HTTP endpoint.

        Args:
            url: The x402-enabled API endpoint URL.
            amount: Dollar amount for the request (e.g. 0.50 for $0.50).
            vendor: Name of the service or vendor.
            justification: Explanation of why this API call is necessary.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            The response body from the paid resource.

        Raises:
            GatewayError: If no x402 gateway is configured, or the payment fails.
            PolicyViolationError: If the policy engine denies the request.
            SpendDeniedError: If a human denies the request (MockX402Gateway).
        """
        if self.x402_gateway is None:
            raise GatewayError(
                "No x402 gateway configured. Pass x402_gateway to AgentWallet."
            )

        on_check = (
            self._audit.start_request(amount, vendor) if self._audit.verbose else None
        )
        result = self.policy_engine.evaluate(
            amount, vendor, justification, on_check=on_check
        )

        if not result.approved:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason=result.denial_reason,
                    checks_passed=result.checks_passed,
                )
            )
            raise PolicyViolationError(result.denial_reason)

        amount_cents = int(round(amount * 100))
        try:
            receipt = self.x402_gateway.execute_x402(
                url,
                amount_cents,
                vendor,
                justification,
                method=method,
                headers=headers,
                body=body,
            )
        except SpendDeniedError:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason="Human denied the x402 payment request",
                    checks_passed=result.checks_passed,
                )
            )
            raise
        except Exception as e:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason=f"Gateway error: {e}",
                    checks_passed=result.checks_passed,
                )
            )
            raise GatewayError(str(e)) from e

        self._audit.log(
            AuditRecord.now(
                agent_id=self.agent_id,
                amount=amount,
                vendor=vendor,
                justification=justification,
                policy_result="approved",
                checks_passed=result.checks_passed,
                gateway_ref=receipt.gateway_ref,
                gateway_type=receipt.gateway_type,
            )
        )

        return receipt.response_body

    @cached_property
    def crewai_tool(self):
        """CrewAI-compatible tool for virtual card spends.

        CrewAI natively accepts LangChain tools, so this wraps ``spend_tool``
        and returns it as a CrewAI ``Tool`` instance for a cleaner integration
        experience.

        Requires ``crewai`` and ``langchain-core``:
        install with ``pip install paygraph[crewai]``.

        Example::

            from crewai import Agent, Task, Crew
            from paygraph import AgentWallet

            wallet = AgentWallet()
            agent = Agent(
                role="Purchasing Agent",
                goal="Buy things",
                tools=[wallet.crewai_tool],
            )
        """
        try:
            from crewai.tools import Tool  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "CrewAI integration requires crewai. "
                "Install it with: pip install paygraph[crewai]"
            )

        lc_tool = self.spend_tool
        return Tool(
            name=lc_tool.name,
            description=lc_tool.description,
            func=lc_tool.run,
        )

    @cached_property
    def x402_tool(self):
        """LangChain-compatible tool for x402 HTTP payments.

        Returns a ``@tool``-decorated function usable in LangGraph agents.
        Requires ``langchain-core`` and an ``x402_gateway`` to be configured.
        """
        return self._build_x402_tool()

    def _build_x402_tool(self):
        try:
            from langchain_core.tools import tool
        except ImportError:
            raise ImportError(
                "LangGraph integration requires langchain-core. "
                "Install it with: pip install paygraph[langgraph]"
            )

        from pydantic import BaseModel, Field

        class X402SpendRequest(BaseModel):
            url: str = Field(description="The x402-enabled API endpoint URL")
            amount: float = Field(description="Dollar amount for the request")
            vendor: str = Field(description="Name of the service/vendor")
            justification: str = Field(description="Why this API call is needed")
            method: str = Field(default="GET", description="HTTP method")

        wallet = self

        @tool("x402_pay", args_schema=X402SpendRequest)
        def x402_pay(
            url: str,
            amount: float,
            vendor: str,
            justification: str,
            method: str = "GET",
        ) -> str:
            """Use this tool to pay for an x402-enabled API endpoint. Provide the URL, dollar amount, vendor name, justification, and HTTP method."""
            try:
                return wallet.request_x402(
                    url, amount, vendor, justification, method=method
                )
            except (PolicyViolationError, SpendDeniedError, GatewayError) as e:
                return f"x402 payment denied: {e}"

        return x402_pay
