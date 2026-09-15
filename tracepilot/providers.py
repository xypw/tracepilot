"""OpenAI 兼容模型接口；只负责生成补丁提案，不拥有工具执行权限。"""

from __future__ import annotations

import json
import re

import httpx


class OpenAICompatibleModelCaller:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _parse_json(content: str) -> dict:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped)
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("模型必须返回JSON对象")
        return parsed

    def __call__(self, prompt: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("模型响应缺少choices[0].message.content") from error
        return self._parse_json(content)
