"""The agent loop: Claude + server-side web tools + our custom tools.

One function, ``run_agent``, drives a task to completion and returns the final
text. It streams a compact progress trace to stderr so the operator can watch
what the model is doing.
"""
from __future__ import annotations

import sys
from typing import Any, Callable

import anthropic

from .config import Settings
from .prompts import system_prompt
from .store import Store
from .tools import SERVER_TOOLS, make_tools

BETAS = ["server-side-fallback-2026-07-01"]


def _trace(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def run_agent(settings: Settings, store: Store, task: str, states: list[str],
              quiet: bool = False, max_iterations: int = 40,
              client: anthropic.Anthropic | None = None,
              on_message: Callable[[Any], None] | None = None) -> str:
    """Run one agent task and return Claude's final text.

    ``client`` is injectable for tests. ``states`` controls which rulebooks are
    loaded into the system prompt (keep it to the states in play so the cached
    prefix stays small and stable).
    """
    client = client or anthropic.Anthropic()
    tools = [*make_tools(settings, store), *SERVER_TOOLS]
    system = [{"type": "text", "text": system_prompt(states), "cache_control": {"type": "ephemeral"}}]

    runner = client.beta.messages.tool_runner(
        model=settings.model,
        max_tokens=32000,
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": task}],
        thinking={"type": "adaptive"},
        output_config={"effort": settings.effort},
        fallbacks="default",
        betas=BETAS,
        max_iterations=max_iterations,
        stream=True,  # long agent turns; the SDK requires streaming above ~10 minutes of output
    )

    final_text: list[str] = []
    last = None
    for item in runner:
        # In streaming mode the runner yields a BetaMessageStream; consume it to a message.
        message = item.get_final_message() if hasattr(item, "get_final_message") else item
        last = message
        if on_message:
            on_message(message)
        for block in message.content:
            btype = getattr(block, "type", "")
            if btype == "text" and block.text.strip():
                _trace(f"[claude] {block.text.strip()[:400]}", quiet)
            elif btype == "tool_use":
                _trace(f"[tool] {block.name} {_short(block.input)}", quiet)
            elif btype == "server_tool_use":
                _trace(f"[web] {block.name} {_short(block.input)}", quiet)
            elif btype == "fallback":
                _trace(f"[fallback] {block.from_.model} declined; {block.to.model} continued", quiet)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            reason = getattr(details, "explanation", None) or "no explanation"
            _trace(f"[refusal] {reason}", quiet)

    if last is None:
        return ""
    if last.stop_reason == "refusal":
        return "The model declined this task. " + (getattr(last.stop_details, "explanation", "") or "")
    if last.stop_reason == "max_tokens":
        _trace("[warn] response hit max_tokens; output may be truncated", quiet)
    for block in last.content:
        if getattr(block, "type", "") == "text":
            final_text.append(block.text)
    usage = getattr(last, "usage", None)
    if usage is not None:
        _trace(f"[usage] in={usage.input_tokens} out={usage.output_tokens} "
               f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}", quiet)
    return "\n".join(final_text).strip()


def _short(obj: Any, n: int = 160) -> str:
    s = str(obj)
    return s if len(s) <= n else s[: n - 3] + "..."
