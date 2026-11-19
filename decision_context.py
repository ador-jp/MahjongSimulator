"""打牌局面を、学習可能な安定したカテゴリへ変換する。"""

from __future__ import annotations


def classify_context(
    hand: list[str],
    context: dict | None,
    *,
    shanten: int,
) -> dict[str, str | int]:
    context = context or {}
    wall_count = int(context.get("wall_count", 70))
    if wall_count > 46:
        phase = "early"
    elif wall_count > 23:
        phase = "middle"
    else:
        phase = "late"

    player = context.get("player")
    riichi = context.get("riichi", {}) or {}
    riichi_count = sum(
        1 for name, declared in riichi.items()
        if declared and name != player
    )
    if riichi_count == 0:
        pressure = "no_riichi"
    elif riichi_count == 1:
        pressure = "one_riichi"
    else:
        pressure = "multi_riichi"

    if shanten <= 0:
        distance = "tenpai"
    elif shanten == 1:
        distance = "one_shanten"
    else:
        distance = "two_plus"

    position = "close"
    scores = context.get("scores", {}) or {}
    if player in scores:
        others = [score for name, score in scores.items() if name != player]
        if others:
            lead = scores[player] - max(others)
            if lead >= 8000:
                position = "leading"
            elif scores[player] - min(others) <= -8000:
                position = "trailing"

    key = ":".join((phase, pressure, distance, position))
    return {
        "key": key,
        "phase": phase,
        "pressure": pressure,
        "distance": distance,
        "position": position,
        "shanten": shanten,
        "wall_count": wall_count,
        "riichi_count": riichi_count,
        "hand_size": len(hand),
    }


def context_label(features: dict) -> str:
    labels = {
        "early": "序盤",
        "middle": "中盤",
        "late": "終盤",
        "no_riichi": "リーチなし",
        "one_riichi": "1軒リーチ",
        "multi_riichi": "複数リーチ",
        "tenpai": "テンパイ",
        "one_shanten": "1シャンテン",
        "two_plus": "2シャンテン以上",
        "leading": "リード",
        "close": "接戦",
        "trailing": "ビハインド",
    }
    return " / ".join(
        labels.get(features.get(name), str(features.get(name, "")))
        for name in ("phase", "pressure", "distance", "position")
    )
