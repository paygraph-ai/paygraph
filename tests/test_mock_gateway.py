import pytest

from paygraph.exceptions import SpendDeniedError
from paygraph.gateways.mock import MockGateway


class TestAutoApprove:
    def test_mints_card_without_prompt(self):
        gw = MockGateway(auto_approve=True)
        card = gw.execute(420, "Anthropic", "test")
        assert card.pan == "4111111111111111"
        assert card.cvv == "123"
        assert card.expiry == "12/28"
        assert card.spend_limit_cents == 420
        assert card.gateway_type == "mock"
        assert card.gateway_ref.startswith("mock_")

    def test_unique_tokens(self):
        gw = MockGateway(auto_approve=True)
        c1 = gw.execute(100, "v", "m")
        c2 = gw.execute(100, "v", "m")
        assert c1.gateway_ref != c2.gateway_ref


class TestManualApproval:
    def test_y_approves(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        gw = MockGateway(auto_approve=False)
        card = gw.execute(100, "vendor", "memo")
        assert card.pan == "4111111111111111"

    def test_empty_approves(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        gw = MockGateway(auto_approve=False)
        card = gw.execute(100, "vendor", "memo")
        assert card.pan == "4111111111111111"

    def test_yes_approves(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        gw = MockGateway(auto_approve=False)
        card = gw.execute(100, "vendor", "memo")
        assert card.pan is not None

    def test_n_denies(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        gw = MockGateway(auto_approve=False)
        with pytest.raises(SpendDeniedError, match="denied"):
            gw.execute(100, "vendor", "memo")

    def test_no_denies(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "no")
        gw = MockGateway(auto_approve=False)
        with pytest.raises(SpendDeniedError):
            gw.execute(100, "vendor", "memo")


class TestRevoke:
    def test_revoke_existing(self):
        gw = MockGateway(auto_approve=True)
        card = gw.execute(100, "v", "m")
        assert gw.revoke(card.gateway_ref) is True

    def test_revoke_nonexistent(self):
        gw = MockGateway(auto_approve=True)
        assert gw.revoke("fake_token") is False

    def test_revoke_twice(self):
        gw = MockGateway(auto_approve=True)
        card = gw.execute(100, "v", "m")
        assert gw.revoke(card.gateway_ref) is True
        assert gw.revoke(card.gateway_ref) is False
