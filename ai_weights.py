"""
AI評価の重みパラメータを管理するモジュール。
game_logic.py と game_log.py の両方からインポートされ、
循環インポートを避けるための共有ストア。
"""
from copy import deepcopy
import json
from pathlib import Path

_WEIGHTS_FILE = Path(__file__).parent / "ai_weights.json"

DEFAULT_WEIGHTS: dict = {
    # 牌効率を主軸にしつつ、同シャンテン内で受け入れ・ドラ・安全度を比較する。
    "sh_table": {0: 100, 1: 65, 2: 38, 3: 20, 4: 10, 5: 4, 6: 0},
    "safety_base": 0.05,
    "safety_per_riichi": 0.15,
    # 残り24枚の受け入れで上限20点。旧値は1枚で1000点に飽和していた。
    "acc_max": 20.0,
    "acc_norm": 24.0,
    "dora_per_tile": 4.0,
    "dora_max": 20.0,
    "dora_discard_penalty": 8.0,
    "late_wall": 30,
    "very_late_wall": 10,
    "riichi_late_bonus": 18.0,
    "lead_threshold": 3000,
    "lead_late_bonus": 12.0,
    "very_late_bonus": 15.0,
    "tenpai_bonus": 6.0,
    "riichi_eligible_bonus": 4.0,
    "context_profiles": {},
}

# 実行時に変更されるメインの重みストア
_weights: dict = deepcopy(DEFAULT_WEIGHTS)


def get() -> dict:
    """現在の重みを返す"""
    return _weights

def snapshot() -> dict:
    """評価中に安全に保持できる重みのディープコピーを返す。"""
    return deepcopy(_weights)


def replace(weights: dict) -> None:
    """重み全体を置き換える。A/B評価中の復元にも使用する。"""
    _weights.clear()
    _weights.update(deepcopy(weights))


def resolve(context_key: str | None = None, weights: dict | None = None) -> dict:
    """基本重みに局面プロファイルを重ねた評価用重みを返す。"""
    source = weights if weights is not None else _weights
    resolved = {
        key: deepcopy(value)
        for key, value in source.items()
        if key != "context_profiles"
    }
    if context_key:
        profile = source.get("context_profiles", {}).get(context_key, {})
        resolved.update(deepcopy(profile))
    return resolved


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
    temporary = _WEIGHTS_FILE.with_suffix(f"{_WEIGHTS_FILE.suffix}.tmp")
    temporary.write_text(
        json.dumps(_weights, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(_WEIGHTS_FILE)


def reset_to_default() -> None:
    """デフォルト重みにリセットする"""
    _weights.clear()
    _weights.update(deepcopy(DEFAULT_WEIGHTS))
    if _WEIGHTS_FILE.exists():
        _WEIGHTS_FILE.unlink()
