import os

import pytest

from meridian.models import SupportAgentResponse
from meridian.support_agent import build_support_agent, run_support_agent_turn


def test_support_agent_response_model():
    r = SupportAgentResponse(reply_markdown="Hello", tools_were_used=True)
    assert r.reply_markdown == "Hello" and r.tools_were_used is True


def test_build_support_agent_has_structured_output(monkeypatch):
    monkeypatch.delenv("MERIDIAN_ENABLE_ORDER_SUBMIT", raising=False)
    agent = build_support_agent()
    assert agent.output_type is SupportAgentResponse
    assert len(agent.tools) == 6


def test_build_support_agent_includes_create_order_when_enabled(monkeypatch):
    monkeypatch.setenv("MERIDIAN_ENABLE_ORDER_SUBMIT", "true")
    agent = build_support_agent()
    names = {t.name for t in agent.tools}
    assert "meridian_create_order" in names
    assert len(agent.tools) == 7


@pytest.mark.skipif(not (os.environ.get("OPENAI_API_KEY") or "").strip(), reason="OPENAI_API_KEY not set")
def test_run_support_agent_turn_smoke():
    turn = run_support_agent_turn(
        [{"role": "user", "content": "Say hello in one short sentence—no tools."}],
        acting_customer_id="004cebc1-65d8-4190-99fb-30245b11bc1c",
    )
    assert isinstance(turn, SupportAgentResponse)
    assert len(turn.reply_markdown.strip()) > 0
