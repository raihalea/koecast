"""riva_bridge.py の純粋ロジック (CJK 空白除去) の単体テスト。

Riva NIM への結合は別途 server/tests/manual/smoke.py で観測する (Riva 起動必要)。
"""
from __future__ import annotations

import pytest

from dictation_gateway.riva_bridge import remove_cjk_spaces


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 検証フェーズで観測した出力そのまま
        ("ベ ッ ド ロ ッ ク で ラ ム ダ", "ベッドロックでラムダ"),
        ("書 き た い で す。", "書きたいです。"),
        # 英字 (ラテン文字) と CJK の境界の空白は維持される
        ("AI で 動 か す", "AI で動かす"),
        ("Riva の partial を 出 力", "Riva の partial を出力"),
        # 全角文字どうしの空白も除去
        ("コ ン テ ナ", "コンテナ"),
        # 既に空白なしのテキストはそのまま
        ("こんにちは。", "こんにちは。"),
        # 英文のみ (CJK が含まれない) は変化なし
        ("hello world", "hello world"),
        # 空文字
        ("", ""),
        # 半角数字を CJK の合間に置いた場合 — 数字も空白維持側に倒したい:
        # ("数 字 1 2 3 を含む" のような) 半角数字は CJK_CLASS に入らないので空白維持。
        ("数 字 1 2 3", "数字 1 2 3"),
    ],
)
def test_remove_cjk_spaces(raw: str, expected: str) -> None:
    assert remove_cjk_spaces(raw) == expected
