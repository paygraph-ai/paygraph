"""Tests for the CrewAI integration (wallet.crewai_tool)."""

from unittest.mock import MagicMock, patch

import pytest

from paygraph import AgentWallet
from paygraph.gateways.mock import MockGateway


class TestCrewAITool:
    """Test wallet.crewai_tool integration."""

    def test_crewai_tool_raises_import_error_without_crewai(self):
        """crewai_tool raises a helpful ImportError when crewai is not installed."""
        wallet = AgentWallet(gateway=MockGateway(auto_approve=True))

        with patch.dict("sys.modules", {"crewai": None, "crewai.tools": None}):
            # Clear cached property so it re-evaluates
            wallet.__dict__.pop("crewai_tool", None)
            with pytest.raises(ImportError, match="pip install paygraph\\[crewai\\]"):
                _ = wallet.crewai_tool

    def test_crewai_tool_returns_tool_with_correct_metadata(self):
        """crewai_tool wraps spend_tool and exposes name and description."""
        mock_crewai_tool_cls = MagicMock()
        mock_tool_instance = MagicMock()
        mock_crewai_tool_cls.return_value = mock_tool_instance

        mock_crewai = MagicMock()
        mock_crewai.tools = MagicMock()
        mock_crewai.tools.Tool = mock_crewai_tool_cls

        wallet = AgentWallet(gateway=MockGateway(auto_approve=True))
        # Clear cached properties
        wallet.__dict__.pop("crewai_tool", None)
        wallet.__dict__.pop("spend_tool", None)

        with patch.dict(
            "sys.modules", {"crewai": mock_crewai, "crewai.tools": mock_crewai.tools}
        ):
            tool = wallet.crewai_tool

        # Verify CrewAI Tool was constructed with the right args
        mock_crewai_tool_cls.assert_called_once()
        call_kwargs = mock_crewai_tool_cls.call_args.kwargs
        assert call_kwargs["name"] == "mint_virtual_card"
        assert (
            "spend" in call_kwargs["description"].lower()
            or "purchase" in call_kwargs["description"].lower()
            or "money" in call_kwargs["description"].lower()
        )
        assert callable(call_kwargs["func"])
        assert tool is mock_tool_instance
