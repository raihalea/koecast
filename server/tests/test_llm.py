"""段階6-2-c: llm.py の決定的な部分の単体テスト。

実 LLM (qwen36-mtp 等) には依存せず、httpx.MockTransport で HTTP レイヤを mock する。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from dictation_gateway.config import Config
from dictation_gateway.llm import (
    DEFAULT_SYSTEM_PROMPT,
    GLOSSARY_HEADER,
    FormattedResult,
    LlmClient,
    build_messages,
)


# ----------------------------------------------------------------------
# build_messages — 純粋関数
# ----------------------------------------------------------------------


def test_build_messages_no_glossary_no_user_prompt() -> None:
    msgs = build_messages("こんにちは", system_prompt="", glossary_terms=[])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == DEFAULT_SYSTEM_PROMPT
    assert msgs[1]["role"] == "user"
    assert "こんにちは" in msgs[1]["content"]
    assert "次のテキストを整形" in msgs[1]["content"]


def test_build_messages_with_glossary_terms() -> None:
    msgs = build_messages(
        "ベッドロックでラムダ",
        system_prompt="",
        glossary_terms=["Bedrock", "Lambda", "DynamoDB"],
    )
    sys_content = msgs[0]["content"]
    assert DEFAULT_SYSTEM_PROMPT in sys_content
    assert GLOSSARY_HEADER in sys_content
    # 各用語が system に含まれる
    assert "Bedrock" in sys_content
    assert "Lambda" in sys_content
    assert "DynamoDB" in sys_content


def test_build_messages_with_user_system_prompt() -> None:
    msgs = build_messages(
        "hi",
        system_prompt="追加の指示テキスト",
        glossary_terms=["X"],
    )
    sys_content = msgs[0]["content"]
    assert DEFAULT_SYSTEM_PROMPT in sys_content
    assert "追加の指示テキスト" in sys_content
    assert "X" in sys_content


# ----------------------------------------------------------------------
# LlmClient.format — モック HTTP で fallback / 成功 / skip パスを検証
# ----------------------------------------------------------------------


def _make_config(*, glossary: list[str] | None = None, system_prompt: str = "") -> Config:
    return Config(
        glossary=list(glossary or []),
        system_prompt=system_prompt,
    )


def _ok_handler(content: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": content}}
                ]
            },
        )
    return handler


@pytest.mark.asyncio
async def test_format_returns_none_when_disabled() -> None:
    client = LlmClient(_make_config(), transport=httpx.MockTransport(_ok_handler("X")))
    result = await client.format("こんにちは", enable_formatting=False)
    assert result is None


@pytest.mark.asyncio
async def test_format_success_returns_content() -> None:
    transport = httpx.MockTransport(_ok_handler("整形済みテキスト"))
    client = LlmClient(_make_config(), transport=transport)
    result = await client.format("生のテキスト", enable_formatting=True)
    assert result == FormattedResult(text="整形済みテキスト", fallback=False)


@pytest.mark.asyncio
async def test_format_strips_whitespace() -> None:
    transport = httpx.MockTransport(_ok_handler("  だぶった空白入り  \n"))
    client = LlmClient(_make_config(), transport=transport)
    result = await client.format("x", enable_formatting=True)
    assert result is not None
    assert result.text == "だぶった空白入り"
    assert result.fallback is False


@pytest.mark.asyncio
async def test_format_fallback_on_http_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = LlmClient(_make_config(), transport=httpx.MockTransport(handler))
    result = await client.format("元の文", enable_formatting=True)
    assert result == FormattedResult(text="元の文", fallback=True)


@pytest.mark.asyncio
async def test_format_fallback_on_empty_content() -> None:
    transport = httpx.MockTransport(_ok_handler(""))
    client = LlmClient(_make_config(), transport=transport)
    result = await client.format("元の文", enable_formatting=True)
    assert result == FormattedResult(text="元の文", fallback=True)


@pytest.mark.asyncio
async def test_format_fallback_on_whitespace_only_content() -> None:
    transport = httpx.MockTransport(_ok_handler("   \n  "))
    client = LlmClient(_make_config(), transport=transport)
    result = await client.format("元の文", enable_formatting=True)
    assert result == FormattedResult(text="元の文", fallback=True)


@pytest.mark.asyncio
async def test_format_fallback_on_malformed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = LlmClient(_make_config(), transport=httpx.MockTransport(handler))
    result = await client.format("元の文", enable_formatting=True)
    assert result == FormattedResult(text="元の文", fallback=True)


@pytest.mark.asyncio
async def test_format_fallback_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    client = LlmClient(_make_config(), transport=httpx.MockTransport(handler))
    result = await client.format("元の文", enable_formatting=True)
    assert result == FormattedResult(text="元の文", fallback=True)


@pytest.mark.asyncio
async def test_format_sends_glossary_in_system_prompt() -> None:
    """context_terms と config.glossary が integration されて system prompt に入る。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _ok_handler("整形済み")(request)

    client = LlmClient(
        _make_config(glossary=["AWS"]),
        transport=httpx.MockTransport(handler),
    )
    await client.format(
        "x",
        enable_formatting=True,
        context_terms=["Bedrock", "Lambda", "AWS"],  # AWS は重複
    )
    msgs = captured["body"]["messages"]
    system = msgs[0]["content"]
    # 重複は除外、出現順は base_glossary → context_terms
    assert "AWS" in system
    assert "Bedrock" in system
    assert "Lambda" in system
    assert system.count("AWS") == 1


@pytest.mark.asyncio
async def test_format_sets_enable_thinking_false() -> None:
    """Qwen3 系 thinking モード抑制フラグが必ず送られる。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _ok_handler("ok")(request)

    client = LlmClient(_make_config(), transport=httpx.MockTransport(handler))
    await client.format("x", enable_formatting=True)
    assert captured["body"].get("chat_template_kwargs") == {"enable_thinking": False}
