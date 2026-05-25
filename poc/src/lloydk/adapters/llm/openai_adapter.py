from __future__ import annotations
from lloydk.config import settings


class OpenAIAdapter:
    name = "openai"

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        import openai
        self.client = openai.OpenAI(
            api_key=api_key or settings.openai_api_key,
            base_url=base_url,
        )
        self.model = model or "gpt-4o"

    def complete(self, system: str, user: str, **opts) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=opts.get("temperature", 0.7),
            max_tokens=opts.get("max_tokens", 4000),
        )
        return resp.choices[0].message.content or ""
