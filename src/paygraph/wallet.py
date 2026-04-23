from functools import cached_property

from paygraph.audit import AuditLogger, AuditRecord
from paygraph.exceptions import (
    GatewayError,
    HumanApprovalRequired,
    PolicyViolationError,
    SpendDeniedError,
)
from paygraph.gateways.base import BaseGateway, CardResult, SpendResult
from paygraph.gateways.mock import MockGateway
from paygraph.gateways.slack import SlackApprovalGateway
from paygraph.policy import PolicyEngine, SpendPolicy


class AgentWallet:
    """Main entry point for PayGraph spend governance.

    Orchestrates policy checks, gateway calls, and audit logging for both
    virtual card and x402 payment flows. All gateways share a single
    ``PolicyEngine``, ensuring a unified daily budget across payment types.

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
        gateways: dict[str, BaseGateway] | BaseGateway | None = None,
        policy: SpendPolicy | None = None,
        agent_id: str = "default",
        log_path: str = "paygraph_audit.jsonl",
        verbose: bool = True,
        animate: bool = False,
    ) -> None:
        """Initialize the wallet with gateways, policy, and audit settings.

        Args:
            gateways: A single gateway, a named dict of gateways, or None
                (defaults to ``{"default": MockGateway()}``). A single gateway
                is auto-wrapped to ``{"default": gw}``.
            policy: Spend policy rules. Defaults to ``SpendPolicy()`` with
                $50 max transaction and $200 daily budget.
            agent_id: Identifier for this agent in audit logs.
            log_path: File path for the JSONL audit log.
            verbose: If True, print policy check results to stdout.
            animate: If True, add a short delay between policy checks
                for visual effect in demos.
        """
        if gateways is None:
            self._gateways: dict[str, BaseGateway] = {"default": MockGateway()}
        elif isinstance(gateways, BaseGateway):
            self._gateways = {"default": gateways}
        else:
            self._gateways = dict(gateways)

        self.policy_engine = PolicyEngine(policy or SpendPolicy())
        self.agent_id = agent_id
        self._audit = AuditLogger(log_path=log_path, verbose=verbose, animate=animate)

    @property
    def gateway(self) -> BaseGateway | None:
        """Backward-compatible alias for the ``"default"`` gateway."""
        return self._gateways.get("default")

    @gateway.setter
    def gateway(self, value: BaseGateway) -> None:
        self._gateways["default"] = value

    def _resolve_gateway(self, name: str) -> BaseGateway:
        """Look up a gateway by name, raising GatewayError if not found."""
        gw = self._gateways.get(name)
        if gw is None:
            raise GatewayError(
                f"No gateway named '{name}' configured. "
                f"Available: {list(self._gateways.keys())}"
            )
        return gw

    def _execute_with_policy(
        self,
        gateway_name: str,
        amount: float,
        vendor: str,
        justification: str,
        **gateway_kwargs,
    ) -> SpendResult:
        """Shared orchestration: policy → Slack check → gateway → commit → audit.

        Args:
            gateway_name: Key into ``self._gateways``.
            amount: Dollar amount to spend.
            vendor: Vendor name.
            justification: Justification string.
            **gateway_kwargs: Extra kwargs passed to ``gateway.execute()``.

        Returns:
            The ``SpendResult`` from the gateway.
        """
        gw = self._resolve_gateway(gateway_name)

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

        # Human approval via Slack (if threshold is configured and gateway supports it)
        amount_cents = int(round(amount * 100))
        if (
            self.policy_engine.policy.require_human_approval_above is not None
            and amount > self.policy_engine.policy.require_human_approval_above
            and isinstance(gw, SlackApprovalGateway)
        ):
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="pending_approval",
                    checks_passed=result.checks_passed,
                )
            )
            try:
                gw.request_approval(
                    amount_cents, vendor, justification, justification=justification
                )
            except HumanApprovalRequired as e:
                e.gateway_name = gateway_name
                raise
            # request_approval always raises HumanApprovalRequired — unreachable

        try:
            spend_result = gw.execute(amount_cents, vendor, justification, **gateway_kwargs)
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

        # Gateway succeeded — now it is safe to commit the spend to the budget
        self.policy_engine.commit_spend(amount)

        self._audit.log(
            AuditRecord.now(
                agent_id=self.agent_id,
                amount=amount,
                vendor=vendor,
                justification=justification,
                policy_result="approved",
                checks_passed=result.checks_passed,
                gateway_ref=spend_result.gateway_ref,
                gateway_type=spend_result.gateway_type,
            )
        )

        return spend_result

    async def _execute_with_policy_async(
        self,
        gateway_name: str,
        amount: float,
        vendor: str,
        justification: str,
        **gateway_kwargs,
    ) -> SpendResult:
        """Async variant of :meth:`_execute_with_policy`.

        Uses ``gateway.execute_async()`` instead of ``gateway.execute()``.
        """
        gw = self._resolve_gateway(gateway_name)

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
            spend_result = await gw.execute_async(
                amount_cents, vendor, justification, **gateway_kwargs
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

        self.policy_engine.commit_spend(amount)

        self._audit.log(
            AuditRecord.now(
                agent_id=self.agent_id,
                amount=amount,
                vendor=vendor,
                justification=justification,
                policy_result="approved",
                checks_passed=result.checks_passed,
                gateway_ref=spend_result.gateway_ref,
                gateway_type=spend_result.gateway_type,
            )
        )

        return spend_result

    def request_spend(
        self,
        amount: float,
        vendor: str,
        justification: str,
        gateway: str = "default",
    ) -> str:
        """Request a policy-checked virtual card spend.

        Evaluates the spend against the configured policy, then calls the
        card gateway to mint a virtual card if approved.

        Args:
            amount: Dollar amount to spend (e.g. 4.20 for $4.20).
            vendor: Name of the vendor or service.
            justification: Explanation of why this purchase is necessary.
            gateway: Name of the gateway to use (default ``"default"``).

        Returns:
            For most gateways, a string with card details (PAN, CVV, expiry).
            For ``stripe_mpp_*`` gateways, a string with the SPT id and spend limit.

        Raises:
            PolicyViolationError: If the policy engine denies the request.
            SpendDeniedError: If a human denies the request (MockGateway).
            GatewayError: If the gateway API call fails.
            HumanApprovalRequired: If the amount exceeds
                ``policy.require_human_approval_above`` and a
                ``SlackApprovalGateway`` is configured. Resume with
                ``complete_spend(request_id, approved=True)``.
        """
        spend_result = self._execute_with_policy(
            gateway, amount, vendor, justification
        )

        if isinstance(spend_result, CardResult):
            if spend_result.gateway_type.startswith("stripe_mpp"):
                return (
                    f"SPT approved. Token: {spend_result.gateway_ref} "
                    f"(spend limit: ${amount:.2f})"
                )
            return (
                f"Card approved. PAN: {spend_result.pan}, "
                f"CVV: {spend_result.cvv}, Expiry: {spend_result.expiry}"
            )

        # Fallback for non-card gateways
        return f"Spend approved. Ref: {spend_result.gateway_ref}"

    def complete_spend(
        self,
        request_id: str,
        approved: bool,
        gateway: str = "default",
    ) -> str:
        """Resume a spend that was paused for human Slack approval.

        Call this after catching ``HumanApprovalRequired`` from
        ``request_spend()`` and receiving the human's response.

        Args:
            request_id: The ``request_id`` from the ``HumanApprovalRequired``
                exception.
            approved: ``True`` to approve the spend, ``False`` to deny it.
            gateway: Name of the gateway that initiated the approval.
                Use ``e.gateway_name`` from the ``HumanApprovalRequired``
                exception to resolve the correct gateway.

        Returns:
            Card details string if approved (same format as ``request_spend``).

        Raises:
            GatewayError: If the gateway is not a ``SlackApprovalGateway``.
            SpendDeniedError: If ``approved`` is ``False``.
        """
        gw = self._resolve_gateway(gateway)
        if not isinstance(gw, SlackApprovalGateway):
            raise GatewayError("complete_spend requires a SlackApprovalGateway.")

        # Peek at pending metadata before complete_spend() pops it
        pending = gw.get_pending(request_id)
        amount = pending["amount_cents"] / 100
        vendor = pending["vendor"]
        justification = pending["justification"]

        try:
            card = gw.complete_spend(request_id, approved)
        except SpendDeniedError:
            self._audit.log(
                AuditRecord.now(
                    agent_id=self.agent_id,
                    amount=amount,
                    vendor=vendor,
                    justification=justification,
                    policy_result="denied",
                    denial_reason=f"Human denied spend of ${amount:.2f} for {vendor}",
                )
            )
            raise

        self.policy_engine.commit_spend(amount)
        self._audit.log(
            AuditRecord.now(
                agent_id=self.agent_id,
                amount=amount,
                vendor=vendor,
                justification=justification,
                policy_result="approved",
                gateway_ref=card.gateway_ref,
                gateway_type=card.gateway_type,
            )
        )

        if card.gateway_type.startswith("stripe_mpp"):
            return (
                f"SPT approved. Token: {card.gateway_ref} (spend limit: ${amount:.2f})"
            )

        if isinstance(card, CardResult):
            return f"Card approved. PAN: {card.pan}, CVV: {card.cvv}, Expiry: {card.expiry}"

        return f"Spend approved. Ref: {card.gateway_ref}"

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
        gateway: str = "x402",
    ) -> str:
        """Make a policy-checked x402 payment to a paid HTTP endpoint (sync).

        Safe to call from non-async code. **Do not call from a running event
        loop** (LangGraph agents, FastAPI, Jupyter) — use
        ``await wallet.request_x402_async(...)`` instead, or use
        ``wallet.x402_tool`` which handles this automatically.

        Args:
            url: The x402-enabled API endpoint URL.
            amount: Dollar amount for the request (e.g. 0.50 for $0.50).
            vendor: Name of the service or vendor.
            justification: Explanation of why this API call is necessary.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.
            gateway: Name of the x402 gateway (default ``"x402"``).

        Returns:
            The response body from the paid resource.

        Raises:
            GatewayError: If no x402 gateway is configured, or the payment fails.
            PolicyViolationError: If the policy engine denies the request.
            SpendDeniedError: If a human denies the request (MockX402Gateway).
        """
        kwargs: dict = {"url": url, "method": method}
        if headers:
            kwargs["headers"] = headers
        if body:
            kwargs["body"] = body

        spend_result = self._execute_with_policy(
            gateway, amount, vendor, justification, **kwargs
        )

        return spend_result.response_body

    async def request_x402_async(
        self,
        url: str,
        amount: float,
        vendor: str,
        justification: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
        gateway: str = "x402",
    ) -> str:
        """Make a policy-checked x402 payment to a paid HTTP endpoint (async).

        Use this coroutine from async contexts such as LangGraph agents,
        FastAPI handlers, or Jupyter notebooks where an event loop is already
        running. The ``x402_tool`` property uses this method automatically.

        Args:
            url: The x402-enabled API endpoint URL.
            amount: Dollar amount for the request (e.g. 0.50 for $0.50).
            vendor: Name of the service or vendor.
            justification: Explanation of why this API call is necessary.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.
            gateway: Name of the x402 gateway (default ``"x402"``).

        Returns:
            The response body from the paid resource.

        Raises:
            GatewayError: If no x402 gateway is configured, or the payment fails.
            PolicyViolationError: If the policy engine denies the request.
            SpendDeniedError: If a human denies the request (MockX402Gateway).
        """
        kwargs: dict = {"url": url, "method": method}
        if headers:
            kwargs["headers"] = headers
        if body:
            kwargs["body"] = body

        spend_result = await self._execute_with_policy_async(
            gateway, amount, vendor, justification, **kwargs
        )

        return spend_result.response_body

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
        Requires ``langchain-core`` and an x402 gateway to be configured.
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

        async def _async_x402_pay(
            url: str,
            amount: float,
            vendor: str,
            justification: str,
            method: str = "GET",
        ) -> str:
            try:
                return await wallet.request_x402_async(
                    url, amount, vendor, justification, method=method
                )
            except (PolicyViolationError, SpendDeniedError, GatewayError) as e:
                return f"x402 payment denied: {e}"

        x402_pay.coroutine = _async_x402_pay

        return x402_pay
