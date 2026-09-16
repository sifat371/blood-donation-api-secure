"""Chat-facing registry coverage for P2 commitment tools."""

import json

from app.mcp_tools.tools import TOOL_DEFINITIONS
from app.services.chat_agent import SYSTEM_PROMPT, _extract_tool_call


def test_chat_registry_exposes_withdraw_and_confirm_donation():
    definitions = {item["name"]: item for item in TOOL_DEFINITIONS}
    assert "ConfirmDonation" in definitions
    assert "withdraw" in definitions["UpdateRequest"]["parameters"]["action"]

    rendered = SYSTEM_PROMPT.format(tools=json.dumps(TOOL_DEFINITIONS))
    assert "ConfirmDonation" in rendered
    assert "withdraw" in rendered


def test_chat_parser_accepts_confirm_donation_tool_call():
    parsed = _extract_tool_call(
        '```tool_call\n'
        '{"tool":"ConfirmDonation","args":{"request_id":12,"commitment_id":34}}'
        '\n```'
    )
    assert parsed == (
        "ConfirmDonation",
        {"request_id": 12, "commitment_id": 34},
    )
