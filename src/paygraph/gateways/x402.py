import asyncio
import base64
import concurrent.futures
import contextvars
import json

from paygraph.gateways.base import BaseGateway, X402Result

# Deprecated alias — kept for one release cycle
X402Receipt = X402Result

# Policy-approved amount (in cents) for the x402 request currently in flight.
# Scoped per task so concurrent requests through one gateway can't see each
# other's cap.
_approved_cents: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "paygraph_x402_approved_cents", default=None
)


def _asset_decimals(network: str, asset: str) -> int | None:
    """Return the decimals of a known x402 USD stablecoin, or ``None``.

    Only assets registered in the x402 SDK as the default stablecoin for
    their network are recognised. Anything else can't be valued in USD, so
    callers must treat ``None`` as "not payable".
    """
    lookups = []
    try:
        from x402.mechanisms.evm.utils import get_asset_info as evm_asset_info
        from x402.mechanisms.evm.v1.utils import get_asset_info as evm_v1_asset_info

        lookups += [evm_asset_info, evm_v1_asset_info]
    except ImportError:
        pass
    try:
        from x402.mechanisms.svm.utils import get_asset_info as svm_asset_info

        lookups.append(svm_asset_info)
    except ImportError:
        pass

    for lookup in lookups:
        try:
            return int(lookup(network, asset)["decimals"])
        except (ValueError, KeyError, TypeError):
            continue
    return None


def _requirement_within_cap(requirement, cap_cents: int) -> bool:
    """True if ``requirement`` costs at most ``cap_cents`` in a known stablecoin."""
    decimals = _asset_decimals(
        getattr(requirement, "network", ""), getattr(requirement, "asset", "")
    )
    if decimals is None:
        return False
    try:
        required_atomic = int(requirement.get_amount())
    except (TypeError, ValueError):
        return False
    max_atomic = cap_cents * 10**decimals // 100
    return 0 <= required_atomic <= max_atomic


class X402Gateway(BaseGateway):
    """Gateway for x402 HTTP 402 payments using on-chain USDC.

    Supports EVM chains (Base, Ethereum, etc.) and Solana. At least one
    private key must be provided. The x402 SDK is imported lazily — it
    only needs to be installed when this gateway is used.

    Args:
        evm_private_key: Hex-encoded EVM private key (e.g. ``"0x..."``).
        svm_private_key: Base58-encoded Solana private key.
    """

    def __init__(
        self,
        evm_private_key: str | None = None,
        svm_private_key: str | None = None,
    ) -> None:
        if not evm_private_key and not svm_private_key:
            raise ValueError(
                "At least one of evm_private_key or svm_private_key is required"
            )

        try:
            from x402 import x402Client
        except ImportError:
            raise ImportError(
                "x402 integration requires the x402 SDK. "
                "Install it with: pip install paygraph[x402]"
            )

        self._client = x402Client()

        if evm_private_key:
            from eth_account import Account
            from x402.mechanisms.evm import EthAccountSigner
            from x402.mechanisms.evm.exact.register import register_exact_evm_client

            account = Account.from_key(evm_private_key)
            register_exact_evm_client(self._client, EthAccountSigner(account))
            self._payer = account.address

        if svm_private_key:
            from x402.mechanisms.svm import KeypairSigner
            from x402.mechanisms.svm.exact.register import register_exact_svm_client

            svm_signer = KeypairSigner.from_base58(svm_private_key)
            register_exact_svm_client(self._client, svm_signer)
            if not evm_private_key:
                self._payer = str(svm_signer.address)

        self._client.register_policy(self._filter_requirements_over_cap)
        self._client.on_before_payment_creation(self._abort_if_over_cap)

    @staticmethod
    def _filter_requirements_over_cap(version: int, requirements: list) -> list:
        """x402 policy: drop requirements that exceed the approved amount.

        Runs before the SDK selects a requirement, so a cheaper acceptable
        option offered by the server can still be chosen.
        """
        cap_cents = _approved_cents.get()
        if cap_cents is None:
            return []
        return [r for r in requirements if _requirement_within_cap(r, cap_cents)]

    @staticmethod
    def _abort_if_over_cap(context):
        """x402 before-payment hook: final check on the selected requirement.

        Runs after selection and before any payload is signed.
        """
        from x402 import AbortResult

        cap_cents = _approved_cents.get()
        selected = context.selected_requirements
        if cap_cents is None or not _requirement_within_cap(selected, cap_cents):
            return AbortResult(
                reason=(
                    f"x402 requirement of {selected.get_amount()} atomic units of "
                    f"{selected.asset} on {selected.network} exceeds the "
                    f"policy-approved amount of {cap_cents} cents"
                )
            )
        return None

    async def execute_async(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Make a paid HTTP request via the x402 protocol (async).

        Use this coroutine directly from async contexts such as LangGraph
        agents, FastAPI handlers, or Jupyter notebooks where an event loop
        is already running. Calling the synchronous :meth:`execute`
        from those contexts will raise ``RuntimeError``.

        Args:
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            url: The x402-enabled endpoint URL.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Result`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry),
                or the server's payment requirement exceeds ``amount_cents``
                or uses an asset that can't be valued in USD. In that case
                nothing is signed or sent.
        """

        from x402.http.clients import x402HttpxClient

        token = _approved_cents.set(amount_cents)
        try:
            return await self._request(
                x402HttpxClient, amount_cents, url, method, headers, body
            )
        finally:
            _approved_cents.reset(token)

    async def _request(
        self,
        client_cls,
        amount_cents: int,
        url: str,
        method: str,
        headers: dict | None,
        body: str | None,
    ) -> X402Result:
        from x402 import NoMatchingRequirementsError, PaymentAbortedError

        async with client_cls(self._client) as http:
            req_kwargs: dict = {}
            if headers:
                req_kwargs["headers"] = headers
            if body:
                req_kwargs["content"] = body

            try:
                response = await http.request(method, url, **req_kwargs)
            except Exception as e:
                cause = e.__cause__ or e
                if isinstance(cause, (NoMatchingRequirementsError, PaymentAbortedError)):
                    raise RuntimeError(
                        f"x402 payment refused before signing (approved amount: "
                        f"{amount_cents} cents): {cause}"
                    ) from e
                raise
            await response.aread()

            # If still 402 after the SDK's retry, payment failed
            if response.status_code == 402:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", "Payment required but failed")
                except Exception:
                    error_msg = "Payment required but failed"
                raise RuntimeError(f"x402 payment failed: {error_msg}")

            tx_hash = ""
            network = ""
            settled_amount_atomic: str | None = None
            settled_amount_cents: int | None = None

            # Parse the base64-encoded payment-response header
            payment_header = response.headers.get("payment-response", "")
            if payment_header:
                try:
                    settle = json.loads(base64.b64decode(payment_header))
                    tx_hash = settle.get("transaction", "") or settle.get(
                        "transactionId", ""
                    )
                    network = settle.get("network", "")
                    # x402 SettleResponse.amount is a string in the asset's
                    # smallest atomic unit. USDC/USDT (the x402 default) have
                    # 6 decimals, so 1 cent = 10_000 atomic units.
                    raw_amount = settle.get("amount")
                    if raw_amount is not None:
                        settled_amount_atomic = str(raw_amount)
                        try:
                            settled_amount_cents = int(raw_amount) // 10_000
                        except (TypeError, ValueError):
                            settled_amount_cents = None
                except Exception:
                    pass

            return X402Result(
                url=url,
                amount_cents=amount_cents,
                network=network,
                transaction_hash=tx_hash,
                payer=self._payer,
                gateway_ref=tx_hash or f"x402_{id(response)}",
                gateway_type="x402",
                status_code=response.status_code,
                response_body=response.text,
                content_type=response.headers.get("content-type", ""),
                settled_amount_atomic=settled_amount_atomic,
                settled_amount_cents=settled_amount_cents,
            )

    def execute(
        self,
        amount_cents: int,
        vendor: str,
        memo: str,
        *,
        url: str = "",
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
    ) -> X402Result:
        """Make a paid HTTP request via the x402 protocol (sync).

        Synchronous wrapper around :meth:`execute_async`. Safe to call
        from any context — including scripts, CLI, and environments with a
        running event loop (LangGraph agents, FastAPI handlers, Jupyter
        notebooks). When a loop is already running in the current thread, the
        call is automatically dispatched to a worker thread with its own fresh
        event loop. For fully-async callers that prefer to avoid the thread
        overhead, ``await gateway.execute_async(...)`` is also available.

        Args:
            amount_cents: Payment amount in cents.
            vendor: Name of the vendor or service.
            memo: Justification or memo for the payment.
            url: The x402-enabled endpoint URL.
            method: HTTP method (default ``"GET"``).
            headers: Optional additional HTTP headers.
            body: Optional request body string.

        Returns:
            An ``X402Result`` with the response body and transaction details.

        Raises:
            RuntimeError: If the x402 payment fails (still 402 after retry).
        """
        coro = self.execute_async(
            amount_cents, vendor, memo, url=url, method=method, headers=headers, body=body
        )

        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False

        if running:
            # A loop is already running in this thread (LangGraph, FastAPI, Jupyter,
            # etc.). Dispatch to a worker thread so asyncio.run() can create a fresh
            # event loop there without interfering with the caller's loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()

        return asyncio.run(coro)
