"""段階6-2-b: Riva ASR NIM への gRPC ストリーミング中継。

検証フェーズで判明した責務をすべて織り込む (docs/stage6-readiness.md §2.2 / §3.1):
  - CJK 文字間空白除去 (regex で除去、partial / final 両方)
  - segment_id 採番: 接続単位で単調増加、is_final=True ごとにインクリメント、
    再接続でリセット (RivaBridge を作り直す)
  - Word Boosting (speech_contexts) は使わない
    (Parakeet 1.1b RNNT Multilingual の ja に効果がないと検証で確定)

設計:
  - nvidia-riva-client は同期 gRPC API なので Riva ストリーム反復は別スレッドで実行
  - 音声入力: thread-safe な queue.Queue
  - 結果出力: asyncio.Queue (別スレッドからは loop.call_soon_threadsafe で put)
  - RivaBridge: 接続全体の segment_id 採番を持つ
  - _RivaSession: 1 つの start-stop サイクル分のストリーミング状態
    (1 接続内で複数 session が起こり得る — 仕様 §2 「start → stop を何度でも繰り返せる」)
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import grpc
import riva.client

from .config import Config

logger = logging.getLogger(__name__)


# CJK 文字間空白除去用 regex。
# 対象: CJK 句読点 / Hiragana / Katakana / CJK 漢字 / 全角英数記号。
# 英数と CJK の境界 (例: "AI で") の空白は維持する。
_CJK_CLASS = (
    "　-〿"  # CJK Symbols and Punctuation
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "＀-￯"  # Halfwidth and Fullwidth Forms
)
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK_CLASS}])\s+(?=[{_CJK_CLASS}])")


def remove_cjk_spaces(text: str) -> str:
    """Parakeet RNNT Multilingual の ja 出力に挟まる文字間空白を除去する。

    検証で確認 (docs/stage6-readiness.md §2.2):
      "ベ ッ ド ロ ッ ク で ラ ム ダ" → "ベッドロックでラムダ"
      "AI で 動 か す" → "AI で動かす"  (英字との境界は保持)
    """
    return _CJK_SPACE_RE.sub("", text)


@dataclass(frozen=True)
class StreamingResult:
    """riva_bridge → server.py に渡す結果イベント。"""

    is_final: bool
    segment_id: int
    text: str


class RivaUnavailable(Exception):
    """Riva バックエンドに接続できない (gRPC UNAVAILABLE)。"""


class RivaStreamError(Exception):
    """ストリーミング中の Riva 異常終了 (gRPC その他のエラー)。"""


class RivaBridge:
    """1 WebSocket 接続全体での segment_id 採番を管理する。

    1 接続 = 複数 session を含み得る (仕様 §2)。
    segment_id は接続単位で単調増加し、再接続でリセットされる (=新 RivaBridge)。
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._next_segment_id: int = 0

    def open_session(self) -> "_RivaSession":
        session = _RivaSession(self._config, starting_segment_id=self._next_segment_id)
        session.start()
        return session

    def commit_session(self, session: "_RivaSession") -> None:
        """終了した session の segment_id 消費を確定する。"""
        self._next_segment_id += session.finals_seen


class _RivaSession:
    """1 start-stop サイクル分の Riva ストリーミングセッション。

    別スレッドで Riva の sync streaming を回し、asyncio 側からは:
      - feed_audio(chunk): 音声を投入
      - close(): 入力終了
      - results() async generator: 結果イベントを取得
      - finals_seen: この session で観測した final の数
      - current_segment_id(): 進行中の segment_id (エラー時の error.segment_id に使う)
    """

    _SENTINEL = object()

    def __init__(self, config: Config, starting_segment_id: int) -> None:
        self._config = config
        self._segment_id: int = starting_segment_id  # 進行中 segment の id
        self.finals_seen: int = 0  # この session 内の final 数
        self._audio_q: queue.Queue = queue.Queue()
        self._result_q: asyncio.Queue = asyncio.Queue()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed: bool = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run, name="riva-stream", daemon=True
        )
        self._thread.start()

    async def feed_audio(self, chunk: bytes) -> None:
        if self._closed:
            return
        # queue.Queue.put は unbounded なので block しない
        self._audio_q.put(chunk)

    async def close(self) -> None:
        """音声入力終了を Riva に伝える (idempotent)。"""
        if self._closed:
            return
        self._closed = True
        self._audio_q.put(self._SENTINEL)

    async def results(self) -> AsyncIterator[StreamingResult]:
        """イベントを順次返す。stream 終了で StopAsyncIteration、エラー時は raise。"""
        while True:
            item = await self._result_q.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def current_segment_id(self) -> int:
        """現在進行中 (まだ final 未受信) の segment_id。"""
        return self._segment_id

    # --- スレッド内 ---

    def _audio_iter(self):
        while True:
            item = self._audio_q.get()
            if item is self._SENTINEL:
                return
            yield item

    def _post(self, item: object) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._result_q.put_nowait, item)

    def _run(self) -> None:
        try:
            auth = riva.client.Auth(uri=self._config.riva_address)
            asr = riva.client.ASRService(auth)
            recog = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                sample_rate_hertz=self._config.default_sample_rate,
                language_code=self._config.default_language,
                max_alternatives=1,
                enable_automatic_punctuation=True,
                audio_channel_count=1,
            )
            cfg = riva.client.StreamingRecognitionConfig(
                config=recog,
                interim_results=True,
            )
            # NOTE: speech_contexts (Word Boosting) は意図的に使わない。
            # 検証で Parakeet 1.1b RNNT Multilingual の ja に対し効果がないと確定。
            # 用語補正は段階6-2-c の llm.py 側で行う。

            for response in asr.streaming_response_generator(
                audio_chunks=self._audio_iter(),
                streaming_config=cfg,
            ):
                for result in response.results:
                    if not result.alternatives:
                        continue
                    raw = result.alternatives[0].transcript
                    text = remove_cjk_spaces(raw)
                    if not text:
                        # 空白除去で空になった partial は捨てる
                        continue
                    if result.is_final:
                        seg_id = self._segment_id
                        self._segment_id += 1
                        self.finals_seen += 1
                        self._post(
                            StreamingResult(
                                is_final=True, segment_id=seg_id, text=text
                            )
                        )
                    else:
                        self._post(
                            StreamingResult(
                                is_final=False,
                                segment_id=self._segment_id,
                                text=text,
                            )
                        )
        except grpc.RpcError as e:
            code = e.code() if hasattr(e, "code") else None
            if code == grpc.StatusCode.UNAVAILABLE:
                logger.warning("Riva unavailable: %s", e)
                self._post(RivaUnavailable(str(e)))
            else:
                logger.exception("Riva gRPC error")
                self._post(RivaStreamError(f"gRPC {code}: {e}"))
        except Exception as e:
            logger.exception("Riva stream failed")
            self._post(RivaStreamError(str(e)))
        finally:
            self._post(None)
