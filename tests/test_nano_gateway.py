import pytest

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.base import SpendResult
from paygraph.gateways.mock_nano import MockNanoGateway
from paygraph.gateways.nano import NanoResult


class TestMockNanoGatewayAutoApprove:
    def test_returns_nano_result(self):
        gw = MockNanoGateway(auto_approve=True)
        result = gw.execute(
            1_000_000,
            "ExampleAPI",
            "need data",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        assert isinstance(result, SpendResult)
        assert isinstance(result, NanoResult)
        assert result.gateway_type == "nano"
        assert result.gateway_ref.startswith("nano_mock_")
        assert result.block_hash == result.gateway_ref
        assert result.amount_cents == 1_000_000
        assert result.receiver.startswith("nano_")

    def test_validates_receiver(self):
        gw = MockNanoGateway(auto_approve=True)
        with pytest.raises(ValueError, match="receiver must be a nano_"):
            gw.execute(100, "v", "m", receiver="0xbad")

    def test_unique_block_hashes(self):
        gw = MockNanoGateway(auto_approve=True)
        r1 = gw.execute(
            100,
            "v",
            "m",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        r2 = gw.execute(
            100,
            "v",
            "m",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        assert r1.block_hash != r2.block_hash


class TestMockNanoGatewayManualApproval:
    def test_default_does_not_auto_approve(self, monkeypatch):
        # A default-constructed gateway must prompt (auto_approve=False).
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockNanoGateway()
        with pytest.raises(SpendDeniedError, match="denied"):
            gw.execute(
                100,
                "vendor",
                "memo",
                receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
            )

    def test_y_approves(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        gw = MockNanoGateway(auto_approve=False)
        result = gw.execute(
            100,
            "vendor",
            "memo",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        assert result.gateway_type == "nano"

    def test_n_denies(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockNanoGateway(auto_approve=False)
        with pytest.raises(SpendDeniedError, match="denied"):
            gw.execute(
                100,
                "vendor",
                "memo",
                receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
            )


class TestMockNanoGatewaySender:
    def test_default_sender(self):
        gw = MockNanoGateway(auto_approve=True)
        result = gw.execute(
            100,
            "v",
            "m",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        assert result.sender.startswith("nano_")

    def test_custom_sender(self):
        gw = MockNanoGateway(
            auto_approve=True,
            sender="nano_3mockp1592653589793238462643383279502884197169399q9vqe",
        )
        result = gw.execute(
            100,
            "v",
            "m",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
        )
        assert result.sender.startswith("nano_")


class TestNanoGatewayValidation:
    def test_requires_64_char_seed(self):
        from paygraph.gateways.nano import NanoGateway

        with pytest.raises(ValueError, match="64-character"):
            NanoGateway(seed="short")


class TestNanoGatewayExecute:
    """Exercise the real gateway's logic with a mocked node so no XNO moves."""

    def _make_gateway(self, fake_rpc):
        from paygraph.gateways.nano import NanoGateway

        gw = NanoGateway(seed="0" * 64)
        gw._rpc = fake_rpc
        return gw

    def test_builds_send_from_account_info(self):
        fake = FakeNode(
            account_info={
                "frontier": "A" * 64,
                "confirmed_balance": "100000000000000000000000000000",  # 0.1 XNO
                "representative": (
                    "nano_3i1aq1cchnmbn9x5rsbap8b15akfh7wj7pwskuzi7ahz8oq6cobd99d4r3b7"
                ),
            }
        )
        gw = self._make_gateway(fake)
        result = gw.execute(
            100,
            "vendor",
            "memo",
            receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
            amount_xno=0.01,
            # 16 hex chars: skip local proof-of-work generation in the test
            work="abcd" * 4,
        )
        # Fetched account state and sent a block
        assert fake.account_info_calls == 1
        assert fake.process_calls == 1
        assert fake.last_subtype == "send"
        assert result.gateway_type == "nano"
        assert result.block_hash == "FAKEPROCESSEDHASH"

    def test_errors_when_no_frontier(self):
        from paygraph.exceptions import GatewayError

        fake = FakeNode(account_info={})
        gw = self._make_gateway(fake)
        with pytest.raises(GatewayError, match="no confirmed frontier"):
            gw.execute(
                100,
                "vendor",
                "memo",
                receiver="nano_1111111111111111111111111111111111111111111111111111hifc8npp",
                amount_xno=0.01,
            )


class FakeNode:
    """Fake Nano node returning canned responses (no network)."""

    def __init__(self, account_info):
        self._account_info = account_info or {}
        self.account_info_calls = 0
        self.process_calls = 0
        self.last_subtype = ""

    def account_info(self, address):
        self.account_info_calls += 1
        return self._account_info

    def process(self, block, subtype=""):
        self.process_calls += 1
        self.last_subtype = subtype
        return {"hash": "FAKEPROCESSEDHASH"}
