import argparse
import os
import tempfile

from paygraph.exceptions import PolicyViolationError
from paygraph.gateways.mock import MockGateway
from paygraph.policy import SpendPolicy
from paygraph.wallet import AgentWallet


def run_demo() -> None:
    print()
    print("\033[1m=== PayGraph Demo ===\033[0m")
    print("Simulating two agent spend requests with MockGateway.\n")

    # Use a temp file for audit so we don't litter the user's directory
    audit_fd, audit_path = tempfile.mkstemp(suffix=".jsonl", prefix="paygraph_demo_")
    os.close(audit_fd)

    policy = SpendPolicy(
        max_transaction=50.0,
        daily_budget=200.0,
        blocked_vendors=["doordash", "ubereats"],
        require_justification=True,
    )

    wallet = AgentWallet(
        gateway=MockGateway(auto_approve=True),
        policy=policy,
        agent_id="demo-agent",
        log_path=audit_path,
        verbose=True,
        animate=True,
    )

    # Request 1: Should succeed
    print("\n\033[1m--- Request 1: $4.20 for Anthropic API ---\033[0m")
    try:
        result = wallet.request_spend(
            amount=4.20,
            vendor="Anthropic API",
            justification="Need Claude API credits to summarize documents for the user.",
        )
        print(f"  Result: {result}")
    except PolicyViolationError as e:
        print(f"  Denied: {e}")

    # Request 2: Should be denied by policy (amount + blocked vendor)
    print("\n\033[1m--- Request 2: $500.00 for DoorDash ---\033[0m")
    try:
        result = wallet.request_spend(
            amount=500.00,
            vendor="DoorDash",
            justification="Ordering lunch for the team.",
        )
        print(f"  Result: {result}")
    except PolicyViolationError as e:
        print(f"  Denied: {e}")

    # Show audit log
    print(f"\n\033[1m--- Audit Log ({audit_path}) ---\033[0m")
    with open(audit_path) as f:
        for line in f:
            print(f"  {line.rstrip()}")

    # Clean up
    os.unlink(audit_path)

    # Copy-pasteable snippet
    print("\n\033[1m--- Get started ---\033[0m")
    print(
        """
from paygraph import AgentWallet, SpendPolicy

wallet = AgentWallet(
    policy=SpendPolicy(max_transaction=25.0, blocked_vendors=["doordash"]),
)

# Use with LangGraph:
# tool = wallet.spend_tool
# graph = StateGraph(...).add_node("tools", ToolNode([tool]))
"""
    )

    print("⭐  Like PayGraph? Star us → github.com/paygraph-ai/paygraph")
    print()
    print("📣  Share your first run:")
    print("    I just tried PayGraph — open-source spend control for AI agents.")
    print("    Agent requested $4.20 → policy approved → virtual card minted.")
    print("    pip install paygraph && paygraph demo")
    print("    github.com/paygraph-ai/paygraph")
    print()


def run_live_demo(model: str, stripe: bool = False, stripe_mpp: bool = False) -> None:
    """Run a live LangGraph agent demo with a real LLM."""
    # Deferred imports — only needed for --live
    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError:
        print(
            "ERROR: Live demo requires LangGraph extras.\n"
            "Install with: pip install paygraph[live]"
        )
        raise SystemExit(1)

    if model == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
                "Export it with: export ANTHROPIC_API_KEY=sk-ant-..."
            )
            raise SystemExit(1)
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model="claude-sonnet-4-20250514")
    elif model == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print(
                "ERROR: OPENAI_API_KEY environment variable is not set.\n"
                "Export it with: export OPENAI_API_KEY=sk-..."
            )
            raise SystemExit(1)
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model="gpt-4o")
    else:
        print(f"ERROR: Unknown model provider: {model}")
        raise SystemExit(1)

    # Resolve gateway
    if stripe and stripe_mpp:
        print("ERROR: Use only one of --stripe or --stripe-mpp.")
        raise SystemExit(1)

    if stripe:
        from paygraph.gateways.stripe import StripeCardGateway

        stripe_key = os.environ.get("STRIPE_API_KEY")
        if not stripe_key:
            print(
                "ERROR: STRIPE_API_KEY environment variable is not set.\n"
                "Export it with: export STRIPE_API_KEY=sk_test_..."
            )
            raise SystemExit(1)
        currency = os.environ.get("STRIPE_CURRENCY", "usd")
        billing_country = os.environ.get("STRIPE_BILLING_COUNTRY")
        billing_address = (
            {
                "line1": "N/A",
                "city": "N/A",
                "postal_code": "00000",
                "country": billing_country,
            }
            if billing_country
            else None
        )
        cardholder_id = os.environ.get("STRIPE_CARDHOLDER_ID")
        gateway = StripeCardGateway(
            api_key=stripe_key,
            currency=currency,
            billing_address=billing_address,
            cardholder_id=cardholder_id,
        )
        gateway_label = (
            f"StripeCardGateway ({gateway._gateway_type.rsplit('_', 1)[-1]})"
        )
    elif stripe_mpp:
        from paygraph.gateways.stripe_mpp import StripeMPPGateway

        stripe_key = os.environ.get("STRIPE_API_KEY")
        payment_method = os.environ.get("STRIPE_MPP_PAYMENT_METHOD")
        grantee = os.environ.get("STRIPE_MPP_GRANTEE")
        if not stripe_key:
            print(
                "ERROR: STRIPE_API_KEY environment variable is not set.\n"
                "Export it with: export STRIPE_API_KEY=sk_test_..."
            )
            raise SystemExit(1)
        if not payment_method:
            print(
                "ERROR: STRIPE_MPP_PAYMENT_METHOD is not set.\n"
                "Export it with: export STRIPE_MPP_PAYMENT_METHOD=pm_..."
            )
            raise SystemExit(1)
        if not grantee:
            print(
                "ERROR: STRIPE_MPP_GRANTEE is not set.\n"
                "Export it with: export STRIPE_MPP_GRANTEE=profile_..."
            )
            raise SystemExit(1)
        currency = os.environ.get("STRIPE_CURRENCY", "usd")
        expires_env = os.environ.get("STRIPE_MPP_EXPIRES_IN_SECONDS", "3600")
        try:
            expires_in_seconds = int(expires_env)
        except ValueError:
            print(
                "ERROR: STRIPE_MPP_EXPIRES_IN_SECONDS must be an integer.\n"
                "Example: export STRIPE_MPP_EXPIRES_IN_SECONDS=600"
            )
            raise SystemExit(1)

        gateway = StripeMPPGateway(
            api_key=stripe_key,
            payment_method=payment_method,
            grantee=grantee,
            currency=currency,
            expires_in_seconds=expires_in_seconds,
        )
        gateway_label = f"StripeMPPGateway ({gateway._gateway_type.rsplit('_', 1)[-1]})"
    else:
        gateway = MockGateway(auto_approve=True)
        gateway_label = "MockGateway"

    _DIM = "\033[2m"
    _BOLD = "\033[1m"
    _CYAN = "\033[96m"
    _RESET = "\033[0m"

    print()
    print(f"{_BOLD}{'=' * 54}{_RESET}")
    print(f"{_BOLD}  PayGraph Live Demo{_RESET}")
    print(f"{_BOLD}{'=' * 54}{_RESET}")
    print()
    print(f"  {_DIM}LLM{_RESET}      {model}")
    print(f"  {_DIM}Gateway{_RESET}  {gateway_label}")
    print()

    audit_fd, audit_path = tempfile.mkstemp(suffix=".jsonl", prefix="paygraph_live_")
    os.close(audit_fd)

    policy = SpendPolicy(
        max_transaction=50.0,
        daily_budget=200.0,
        blocked_vendors=["doordash", "ubereats"],
        require_justification=True,
    )

    wallet = AgentWallet(
        gateway=gateway,
        policy=policy,
        agent_id="live-demo-agent",
        log_path=audit_path,
        verbose=True,
        animate=True,
    )

    agent = create_react_agent(llm, tools=[wallet.spend_tool])

    task = (
        "You need to purchase $4.20 in Anthropic API credits. "
        "Use the mint_virtual_card tool to complete this purchase. "
        "Provide a clear justification for why you need these credits."
    )

    print(f"  {_DIM}Task{_RESET}     {task}")
    print()
    print(f"  {_CYAN}Invoking agent...{_RESET}")
    print()

    result = agent.invoke({"messages": [("user", task)]})

    # Show agent conversation
    print(f"  {_BOLD}Agent Conversation{_RESET}")
    print(f"  {_DIM}{'─' * 50}{_RESET}")
    for msg in result["messages"]:
        role = msg.__class__.__name__
        content = getattr(msg, "content", "")
        if not content:
            continue

        if role == "HumanMessage":
            label = f"{_BOLD}User{_RESET}"
        elif role == "AIMessage":
            # Skip raw tool_use dicts, only show text
            if isinstance(content, list):
                texts = [
                    c["text"]
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                content = " ".join(texts)
                if not content:
                    continue
            label = f"{_CYAN}Agent{_RESET}"
        elif role == "ToolMessage":
            label = f"{_DIM}Tool{_RESET} "
        else:
            label = f"{_DIM}{role}{_RESET}"

        # Word-wrap long content
        print(f"  {label}  {content}")
        print()
    print(f"  {_DIM}{'─' * 50}{_RESET}")

    # Show audit log path
    print()
    print(f"  {_DIM}Audit log: {audit_path}{_RESET}")
    print()

    print("⭐  Like PayGraph? Star us → github.com/paygraph-ai/paygraph")
    print()
    print("📣  Share your first run:")
    print("    I just tried PayGraph — open-source spend control for AI agents.")
    print("    Agent requested $4.20 → policy approved → virtual card minted.")
    print("    pip install paygraph && paygraph demo")
    print("    github.com/paygraph-ai/paygraph")
    print()

    os.unlink(audit_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="paygraph",
        description="PayGraph — virtual cards for AI agents",
    )
    subparsers = parser.add_subparsers(dest="command")
    demo_parser = subparsers.add_parser(
        "demo", help="Run a simulated demo with MockGateway"
    )
    demo_parser.add_argument(
        "--live",
        action="store_true",
        help="Run a live demo with a real LLM via LangGraph",
    )
    demo_parser.add_argument(
        "--model",
        choices=["anthropic", "openai"],
        default="anthropic",
        help="LLM provider for --live mode (default: anthropic)",
    )
    demo_parser.add_argument(
        "--stripe",
        action="store_true",
        help="Use Stripe Issuing gateway instead of MockGateway (requires STRIPE_API_KEY)",
    )
    demo_parser.add_argument(
        "--stripe-mpp",
        action="store_true",
        help=(
            "Use Stripe MPP gateway instead of MockGateway "
            "(requires STRIPE_API_KEY, STRIPE_MPP_PAYMENT_METHOD, STRIPE_MPP_GRANTEE)"
        ),
    )

    args = parser.parse_args()

    if args.command == "demo":
        if (args.stripe or args.stripe_mpp) and not args.live:
            print("ERROR: --stripe and --stripe-mpp require --live.")
            raise SystemExit(1)
        if args.live:
            run_live_demo(args.model, stripe=args.stripe, stripe_mpp=args.stripe_mpp)
        else:
            run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
