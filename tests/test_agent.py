"""Agent loop test with a fake client: no network, no API key."""
from types import SimpleNamespace

from surplus.agent import run_agent
from surplus.tools import SERVER_TOOLS, make_tools


class _Block(SimpleNamespace):
    pass


class FakeStream:
    def __init__(self, message):
        self._message = message

    def get_final_message(self):
        return self._message


class FakeRunner:
    def __init__(self, messages):
        self._messages = messages

    def __iter__(self):
        return iter(FakeStream(m) for m in self._messages)


class FakeMessages:
    def __init__(self, captured, messages):
        self.captured = captured
        self._messages = messages

    def tool_runner(self, **kwargs):
        self.captured.update(kwargs)
        return FakeRunner(self._messages)


class FakeClient:
    def __init__(self, messages):
        self.captured = {}
        self.beta = SimpleNamespace(messages=FakeMessages(self.captured, messages))


def test_run_agent_passes_config_and_returns_final_text(env):
    settings, store = env
    m1 = _Block(content=[_Block(type="tool_use", name="list_cases", input={}), _Block(type="text", text="looking")],
                stop_reason="tool_use", usage=None)
    m2 = _Block(content=[_Block(type="text", text="Done: 0 leads.")], stop_reason="end_turn",
                usage=_Block(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))
    client = FakeClient([m1, m2])
    out = run_agent(settings, store, "task", ["FL"], quiet=True, client=client)
    assert out == "Done: 0 leads."
    c = client.captured
    assert c["model"] == settings.model
    assert c["fallbacks"] == "default"
    assert c["stream"] is True
    assert c["betas"] == ["server-side-fallback-2026-07-01"]
    assert c["thinking"] == {"type": "adaptive"}
    assert c["output_config"] == {"effort": settings.effort}
    assert c["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Florida" in c["system"][0]["text"] and "Texas" not in c["system"][0]["text"].split("# State rulebooks")[1]
    names = [t.name if hasattr(t, "name") else t["name"] for t in c["tools"]]
    assert "web_search" in names and "save_lead" in names


def test_run_agent_reports_refusal(env):
    settings, store = env
    m = _Block(content=[], stop_reason="refusal", stop_details=_Block(explanation="policy"), usage=None)
    out = run_agent(settings, store, "task", ["TX"], quiet=True, client=FakeClient([m]))
    assert "declined" in out and "policy" in out


def test_tools_have_schemas_and_texas_phone_is_withheld(env):
    settings, store = env
    tools = {t.name: t for t in make_tools(settings, store)}
    for t in tools.values():
        d = t.to_dict()
        assert d["description"] and d["input_schema"]["type"] == "object"
    assert all(t["type"].endswith("20260209") for t in SERVER_TOOLS)
    cid, _ = store.upsert_case(state="TX", county="Dallas", case_number="T1")
    tools["add_contact"].call({"case_id": cid, "name": "P", "relationship": "owner", "confidence": 0.9,
                               "source_url": "https://x", "phone": "555-0100"})
    assert store.contacts(cid)[0]["phone"] is None
