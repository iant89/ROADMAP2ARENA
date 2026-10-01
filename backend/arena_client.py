"""Thin client for arena2api (OpenAI-compatible) with a rolling chat history."""
from __future__ import annotations

from openai import AsyncOpenAI

FORMAT_INSTRUCTIONS = (
    "You will complete ONE step at a time. For every step, output each file you create or\n"
    "modify in full - never partial snippets or diffs. Keep explanations short and put all\n"
    "code inside fenced blocks. Use fenced code blocks with the file path as the language\n"
    "info string, like:"
)


def build_prompt(project_context: str, steps: list[dict], index: int, previous_files: list[str]) -> str:
    """Same format as the frontend mock prompt builder (frontend/src/lib/prompts.js)."""
    step = steps[index - 1]
    current = "\n".join([
        f"CURRENT STEP {index} of {len(steps)}: {step['title']}",
        "DETAILS:",
        step.get("description") or "(no additional details)",
        "",
        "Produce the files for this step now.",
    ])
    if index == 1:
        return "\n".join([
            "You are building a project by following a ROADMAP.md step by step.",
            "",
            "PROJECT CONTEXT:",
            (project_context or "").strip() or "(none provided)",
            "",
            FORMAT_INSTRUCTIONS,
            "```python:src/main.py",
            "<file content>",
            "```",
            "",
            current,
        ])
    files = "\n".join(f"- {p}" for p in previous_files) if previous_files else "- (none yet)"
    return "\n".join([
        "PREVIOUSLY CREATED FILES (do not recreate unless modifying):",
        files,
        "",
        current,
    ])


class ArenaClient:
    """One instance per job; keeps the conversation so later steps see earlier turns."""

    def __init__(self, arena_url: str, model: str, timeout_seconds: float):
        self.model = model
        self.history: list[dict] = []
        # max_retries=0: the orchestrator fails fast and reports the first error.
        self._client = AsyncOpenAI(
            base_url=f"{arena_url.rstrip('/')}/v1",
            api_key="sk-anything",
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def complete(self, prompt: str) -> str:
        messages = [*self.history, {"role": "user", "content": prompt}]
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            stream=False,
        )
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        self.history = [*messages, {"role": "assistant", "content": content}]
        return content

    async def close(self) -> None:
        await self._client.close()
