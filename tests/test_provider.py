from __future__ import annotations

import httpx
import pytest

from tracepilot.providers import OpenAICompatibleModelCaller


def test_provider_parses_structured_response_without_real_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"relative_path":"A.java","old_text":"a","new_text":"b","explanation":"fix"}'
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    caller = OpenAICompatibleModelCaller(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
        client=client,
    )
    assert caller("prompt")["relative_path"] == "A.java"
    client.close()


def test_provider_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON对象"):
        OpenAICompatibleModelCaller._parse_json("[]")
