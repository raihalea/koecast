"""段階6-2-c: ローカル LLM (OpenAI 互換 chat/completions) で `final` を整形する。

設計:
  - 想定エンドポイント: qwen36-mtp の llama-server (OpenAI 互換、`/v1/chat/completions`)
  - クライアントは httpx (非同期)
  - Qwen3 系の thinking モードは `chat_template_kwargs.enable_thinking=false` で抑制する
    (これを付けないと `reasoning_content` だけが返り `content` が空のまま終わる)
  - `enable_formatting=False` のときは何も送らず None を返す (server 側でスキップ)
  - 失敗 (HTTP エラー / タイムアウト / 空 content) 時は `fallback=True` で `final` 原文を返す
    - これはセグメント単位の縮退で、protocol 仕様 §7 「LLM_UNAVAILABLE はセッションを止めず、
      formatted を fallback: true で返し続ける」に従う
    - error メッセージは出さない (LLM_UNAVAILABLE を error で出すかは §5 / §7 に明文の規定が
      ないため、保守的に「出さない」を採用。判断保留事項として報告に明記)

プロンプト方針:
  - System: フィラー除去・用語補正・出力はテキストのみ・config.system_prompt を末尾に
  - 用語辞書 = config.glossary + start.context (重複除去) を「次の用語を優先」セクションで提示
    - 検証で Riva の Word Boosting (speech_contexts) が ja に効かないと確定したため、
      用語補正は本クライアントの責務
  - User: 「次のテキストを整形してください: ...」+ 原文

公開 API:
  - build_messages(final_text, ..., system_prompt, glossary_terms)
      → OpenAI 互換 messages を組み立てる純粋関数 (テスト容易)
  - LlmClient(config).format(final_text, enable_formatting, context_terms)
      → FormattedResult | None  (None は enable_formatting=False のスキップを意味する)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from .config import Config

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "あなたは音声認識結果を自然な日本語に整形するアシスタントです。"
    "フィラー (えーと、あのー、など) を除去し、用語を正しく補正してください。"
    "整形済みテキストのみを返してください。説明や前置きは一切不要です。"
)

GLOSSARY_HEADER = "次の用語を優先的に使ってください:"


def _dedupe_keep_order(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def build_messages(
    final_text: str,
    *,
    system_prompt: str,
    glossary_terms: list[str],
) -> list[dict[str, str]]:
    """OpenAI 互換 API への messages を組み立てる純粋関数 (テスト容易)。

    - system: DEFAULT_SYSTEM_PROMPT + (config.system_prompt) + 用語辞書 (あれば)
    - user: 「次のテキストを整形してください:\\n\\n<final>」
    """
    parts: list[str] = [DEFAULT_SYSTEM_PROMPT]
    if system_prompt:
        parts.append(system_prompt)
    if glossary_terms:
        parts.append(f"{GLOSSARY_HEADER} {', '.join(glossary_terms)}")
    system_content = "\n\n".join(parts)
    return [
        {"role": "system", "content": system_content},
        {
            "role": "user",
            "content": f"次のテキストを整形してください:\n\n{final_text}",
        },
    ]


@dataclass(frozen=True)
class FormattedResult:
    text: str
    fallback: bool


class LlmClient:
    """OpenAI 互換 chat completions で final を formatted に整形するクライアント。

    失敗時は fallback=True で原文を返す (例外を呼び出し側に伝播しない)。
    `transport` 引数はテスト時の httpx.MockTransport 注入用。
    """

    def __init__(
        self,
        config: Config,
        *,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._url = config.llm_url
        self._timeout = timeout
        self._transport = transport
        self._base_system_prompt = config.system_prompt
        self._base_glossary = list(config.glossary)
        # llama.cpp は登録モデルが 1 つなら model 名は任意で動く実装が多い。
        # 明示的に "auto" を入れておく (qwen36-mtp 環境で動作確認済み)。
        self._model_name = "auto"

    async def format(
        self,
        final_text: str,
        *,
        enable_formatting: bool,
        context_terms: Optional[list[str]] = None,
    ) -> Optional[FormattedResult]:
        """formatted の出力 (None なら enable_formatting=False で送出スキップ)。

        失敗時は (final_text, fallback=True) を返す。例外は伝播しない。
        """
        if not enable_formatting:
            return None

        glossary = _dedupe_keep_order(self._base_glossary + list(context_terms or []))
        messages = build_messages(
            final_text,
            system_prompt=self._base_system_prompt,
            glossary_terms=glossary,
        )
        payload = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        try:
            kwargs: dict[str, object] = {"timeout": self._timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.post(self._url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"].get("content")
            if not content or not content.strip():
                logger.warning(
                    "LLM returned empty content; falling back to original"
                )
                return FormattedResult(text=final_text, fallback=True)
            return FormattedResult(text=content.strip(), fallback=False)
        except (
            httpx.HTTPError,
            asyncio.TimeoutError,
            KeyError,
            IndexError,
            ValueError,
        ) as e:
            logger.warning("LLM call failed: %s; falling back to original", e)
            return FormattedResult(text=final_text, fallback=True)
