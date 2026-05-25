from __future__ import annotations
from lloydk.config import settings


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.llm_model or "claude-sonnet-4-6"

    def complete(self, system: str, user: str, **opts) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=opts.get("max_tokens", 4000),
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=opts.get("temperature", 0.7),
        )
        return "".join(block.text for block in msg.content if hasattr(block, "text"))
