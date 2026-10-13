import pytest
from harness.browser.stagehand_engine import BrowserCDPEngine, InteractiveElement


@pytest.mark.asyncio
async def test_browser_cdp_stagehand_primitives():
    browser = BrowserCDPEngine()
    browser.enable_mock_mode([
        InteractiveElement(1, "BUTTON", "button", "Sign In", {"x": 100, "y": 200, "w": 80, "h": 30}),
        InteractiveElement(2, "INPUT", "textbox", "Search Query", {"x": 100, "y": 100, "w": 200, "h": 35}),
    ])

    connected = await browser.connect()
    assert connected is True

    # Observe
    elements = await browser.observe("Sign In")
    assert len(elements) == 1
    assert elements[0].text == "Sign In"

    # Pruned tree rendering
    tree_text = browser.render_pruned_tree(elements)
    assert "[#1] <button>: \"Sign In\"" in tree_text

    # Act
    act_res = await browser.act("Click Sign In", target_index=1)
    assert act_res["success"] is True

    # Extract
    schema = {"properties": {"user": {"type": "string"}, "token": {"type": "string"}}}
    extract_res = await browser.extract(schema, "Extract auth details")
    assert extract_res["status"] == "extracted"
    assert "user" in extract_res["data"]
