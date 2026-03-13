"""
AI評価の重みパラメータを管理するモジュール。
game_logic.py と game_log.py の両方からインポートされ、
循環インポートを避けるための共有ストア。
"""
import json
from pathlib import Path

_WEIGHTS_FILE = Path(__file__).parent / "ai_weights.json"

DEFAULT_WEIGHTS: dict = {
    # シャンテン点テーブル (-1=和了済み, 0=テンパイ, ...)
    # テンパイ(0)が1シャンテン(1)より受け入れ点の最大差分(20)を超えて高くなるよう設定

}

# 実行時に変更されるメインの重みストア
_weights: dict = dict(DEFAULT_WEIGHTS)


def get() -> dict:
    """現在の重みを返す"""
    return _weights


def update(new_weights: dict) -> None:
    """重みを部分更新する（_で始まるメタキーは除外）"""
    _weights.update({k: v for k, v in new_weights.items() if not k.startswith("_")})


def load() -> bool:
    """ai_weights.json から重みを読み込む。存在しない場合はデフォルトを使用。"""
    if _WEIGHTS_FILE.exists():
        try:
            loaded = json.loads(_WEIGHTS_FILE.read_text(encoding="utf-8"))
            _weights.update({k: v for k, v in loaded.items() if not k.startswith("_")})
            return True
        except Exception:
            pass
    return False


def save() -> None:
    """現在の重みを ai_weights.json に保存する"""
    _WEIGHTS_FILE.write_text(
        json.dumps(_weights, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def reset_to_default() -> None:
    """デフォルト重みにリセットする"""
    _weights.clear()
    _weights.update(DEFAULT_WEIGHTS)
    if _WEIGHTS_FILE.exists():
        _WEIGHTS_FILE.unlink()
