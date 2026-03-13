"""
麻雀ゲームロジック
- シャンテン計算によるCPU最適打牌
- 和了（ツモ・ロン）判定
- 点数計算
- 副露（ポン・チー・カン）サポート
- AI多面スコアリング（相対100点評価）
- AI思考表示・ホバープレビュー
"""

from mahjong.shanten import Shanten
from mahjong.tile import TilesConverter
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig
from mahjong.meld import Meld
import ai_weights as _aw

shanten_calculator = Shanten()
hand_calculator = HandCalculator()


# ==========================================
# 牌形式の変換ユーティリティ
# ==========================================

def tiles_to_string_parts(tiles):
    man, pin, sou, honors = "", "", "", ""
    for t in tiles:
        base = t.replace("r", "")
        num, suit = base[0], base[1]
        if suit == "m":
            man += num
        elif suit == "p":
            pin += num
        elif suit == "s":
            sou += num
        elif suit == "z":
            honors += num
    return man, pin, sou, honors


def tiles_to_34(tiles):
    man, pin, sou, honors = tiles_to_string_parts(tiles)
    return TilesConverter.string_to_34_array(
        man=man or "", pin=pin or "", sou=sou or "", honors=honors or ""
    )


def tiles_to_136(tiles):
    man, pin, sou, honors = tiles_to_string_parts(tiles)
    return TilesConverter.string_to_136_array(
        man=man or "", pin=pin or "", sou=sou or "", honors=honors or ""
    )


def tile_to_136(tile):
    base = tile.replace("r", "")
    num, suit = base[0], base[1]
    mapping = {"m": "man", "p": "pin", "s": "sou", "z": "honors"}
    kwargs = {mapping[suit]: num}
    for key in ["man", "pin", "sou", "honors"]:
        if key not in kwargs:
            kwargs[key] = ""
    return TilesConverter.string_to_136_array(**kwargs)[0]


# ==========================================
# 牌の正式表記（日本語）への変換
# ==========================================

KANJI_NUM = {
    "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}
SUIT_KANJI = {"m": "萬", "p": "筒", "s": "索"}
HONOR_LABELS = {
    "1z": "東", "2z": "南", "3z": "西", "4z": "北",
    "5z": "白", "6z": "發", "7z": "中",
}


def tile_to_label(tile):
    is_red = "r" in tile
    base = tile.replace("r", "")
    num, suit = base[0], base[1]
    if base in HONOR_LABELS:
        label = HONOR_LABELS[base]
    else:
        num_k = KANJI_NUM.get(num, num)
        suit_k = SUIT_KANJI.get(suit, "")
        label = f"{num_k}{suit_k}"
    if is_red:
        label = f"赤{label}"
    return label


# ==========================================
# シャンテン数計算
# ==========================================

def calculate_shanten(hand_13):
    try:
        tiles_34 = tiles_to_34(hand_13)
        return shanten_calculator.calculate_shanten(tiles_34)
    except Exception:
        return 99


def calculate_shanten_14(hand_14):
    """
    14枚手牌のシャンテン数を返す。
    和了形（完成）の場合は -1、テンパイは 0。
    まず14枚のまま和了チェック（-1判定）し、
    非和了の場合は13枚ループで最小シャンテンを求める。
    """
    # 和了チェック: 14枚のシャンテンが -1 なら完成
    try:
        tiles_34 = tiles_to_34(hand_14)
        sh = shanten_calculator.calculate_shanten(tiles_34)
        if sh == -1:
            return -1
    except Exception:
        pass
    # 非和了: 1枚ずつ除いた13枚の最小シャンテンを返す
    min_shanten = 99
    for i in range(len(hand_14)):
        remaining = hand_14[:i] + hand_14[i + 1:]
        sh = calculate_shanten(remaining)
        if sh < min_shanten:
            min_shanten = sh
    return min_shanten


# ==========================================
# 副露（ポン・チー・カン）ユーティリティ
# ==========================================

def base_tile(tile):
    return tile.replace("r", "")


def can_pon(hand_13, discard_tile):
    bt = base_tile(discard_tile)
    matches = [i for i, t in enumerate(hand_13) if base_tile(t) == bt]
    if len(matches) >= 2:
        return True, matches[:2]
    return False, []


def can_chi(hand_13, discard_tile, caller_is_left=True):
    bt = base_tile(discard_tile)
    if bt[1] == "z":
        return []
    try:
        num = int(bt[0])
        suit = bt[1]
    except ValueError:
        return []

    candidates = []
    patterns = [
        (num - 2, num - 1),
        (num - 1, num + 1),
        (num + 1, num + 2),
    ]
    for a, b in patterns:
        if a < 1 or b > 9:
            continue
        ta = f"{a}{suit}"
        tb = f"{b}{suit}"
        idx_a, idx_b = None, None
        for i, t in enumerate(hand_13):
            if base_tile(t) == ta and idx_a is None:
                idx_a = i
            elif base_tile(t) == tb and idx_b is None:
                idx_b = i
        if idx_a is not None and idx_b is not None:
            candidates.append((idx_a, idx_b))
    return candidates


def can_kan(hand_13, discard_tile=None):
    results = []
    from collections import Counter
    counts = Counter(base_tile(t) for t in hand_13)
    if discard_tile is not None:
        bt = base_tile(discard_tile)
        if counts[bt] == 3:
            idxs = [i for i, t in enumerate(hand_13) if base_tile(t) == bt]
            results.append({"type": "minkan", "tile": bt, "indices": idxs})
    else:
        for bt, cnt in counts.items():
            if cnt == 4:
                idxs = [i for i, t in enumerate(hand_13) if base_tile(t) == bt]
                results.append({"type": "ankan", "tile": bt, "indices": idxs})
    return results


def apply_pon(hand_13, discard_tile, idx1, idx2):
    meld_tiles = [discard_tile, hand_13[idx1], hand_13[idx2]]
    new_hand = list(hand_13)
    for idx in sorted([idx1, idx2], reverse=True):
        new_hand.pop(idx)
    return new_hand, meld_tiles


def apply_chi(hand_13, discard_tile, idx1, idx2):
    return apply_pon(hand_13, discard_tile, idx1, idx2)


def apply_minkan(hand_13, discard_tile, indices):
    meld_tiles = [discard_tile] + [hand_13[i] for i in indices]
    new_hand = list(hand_13)
    for idx in sorted(indices, reverse=True):
        new_hand.pop(idx)
    return new_hand, meld_tiles


def apply_ankan(hand_13, indices):
    meld_tiles = [hand_13[i] for i in indices]
    new_hand = list(hand_13)
    for idx in sorted(indices, reverse=True):
        new_hand.pop(idx)
    return new_hand, meld_tiles


def build_melds_136(melds_info):
    result = []
    for m in melds_info:
        meld_type = m["type"]
        meld_tiles_str = m["tiles"]
        try:
            tiles_136 = [tile_to_136(t) for t in meld_tiles_str]
            if meld_type == "pon":
                meld = Meld(meld_type=Meld.PON, tiles=tiles_136)
            elif meld_type == "chi":
                meld = Meld(meld_type=Meld.CHI, tiles=tiles_136)
            elif meld_type in ("minkan", "ankan"):
                opened = meld_type == "minkan"
                meld = Meld(meld_type=Meld.KAN, tiles=tiles_136, opened=opened)
            else:
                continue
            result.append(meld)
        except Exception:
            continue
    return result


# ==========================================
# AI評価スコアリング（相対100点方式）
# ==========================================

def _tile_safety_score(tile, rivers, current_player, riichi_players=None):
    if not rivers or current_player is None:
        return 30
    safe_count = 0
    riichi_players = riichi_players or {}
    for name, river in rivers.items():
        if name == current_player:
            continue
        bt = base_tile(tile)
        river_bases = [base_tile(r) for r in river]
        if bt in river_bases:
            safe_count += 1
    if safe_count >= 3:
        return 100
    if safe_count == 2:
        return 75
    if safe_count == 1:
        return 50
    bt = base_tile(tile)
    score = _base_safety_by_tile_type(bt)
    riichi_count = sum(1 for p, r in riichi_players.items() if r and p != current_player)
    # 序盤補正: 他家の合計捨て牌が少ないほど安全度を底上げ（リーチなし時のみ）
    if riichi_count == 0:
        total_opponent_discards = sum(
            len(r) for n, r in rivers.items() if n != current_player
        )
        # 0捨て → +45, 9捨て(3×3) → +0 と線形補間
        if total_opponent_discards < 9:
            early_bonus = int((9 - total_opponent_discards) / 9 * 45)
            score = min(100, score + early_bonus)
    score = max(0, score - riichi_count * 10)
    return score


def _base_safety_by_tile_type(bt):
    if bt[1] == "z":
        return 25
    try:
        num = int(bt[0])
    except ValueError:
        return 20
    if num in (1, 9):
        return 35
    if num in (3, 4, 5, 6, 7):
        return 15
    return 22


def _dora_tile(indicator):
    """ドラ表示牌からドラ牌を返す"""
    bt = base_tile(indicator)
    if len(bt) < 2:
        return None
    suit = bt[1]
    try:
        num = int(bt[0])
    except ValueError:
        return None
    if suit in "mps":
        return f"{(num % 9) + 1}{suit}"
    elif suit == "z":
        if num <= 4:   # 風牌: 東南西北循環
            return f"{(num % 4) + 1}z"
        else:          # 三元牌: 白發中循環
            return f"{5 + ((num - 5 + 1) % 3)}z"
    return None


def _count_weighted_acceptance(hand_13, rivers=None):
    """
    有効牌の (種類数, 残り枚数合計) を返す。
    残り枚数は手牌・河で見えている分を差し引いた推定値。
    """
    from collections import Counter
    current_shanten = calculate_shanten(hand_13)
    if current_shanten < 0:
        return 0, 0
    seen = Counter(base_tile(t) for t in hand_13)
    if rivers:
        for n, r in rivers.items():
            for t in r:
                seen[base_tile(t)] += 1
    type_count = 0
    weighted_count = 0
    checked = set()
    all_tiles = ([f"{i}{s}" for s in "mps" for i in range(1, 10)] +
                 [f"{i}z" for i in range(1, 8)])
    for t in all_tiles:
        bt = base_tile(t)
        if bt in checked:
            continue
        checked.add(bt)
        if calculate_shanten_14(hand_13 + [t]) < current_shanten:
            type_count += 1
            weighted_count += max(0, 4 - seen.get(bt, 0))
    return type_count, weighted_count


def _evaluate_hand_raw(hand_14, context=None):
    """
    手牌からの各打牌候補を「素スコア」で評価。
    戻り値: list of dict (ソート済み)
    """
    hand_14 = list(hand_14)  # 外部リストへの参照を切り離してイテレーション中の変更を防ぐ
    rivers = context.get("rivers") if context else None
    scores = context.get("scores") if context else None
    player_name = context.get("player") if context else None
    wall_count = context.get("wall_count", 70) if context else 70
    riichi_players = context.get("riichi") if context else {}
    dora_indicators = context.get("dora_indicators", []) if context else []
    has_melds = bool(context.get("melds")) if context else False

    riichi_count = sum(1 for p, r in (riichi_players or {}).items() if r and p != player_name)

    # ドラ牌セット（表示牌の次の牌）
    dora_set = set()
    for ind in dora_indicators:
        d = _dora_tile(ind)
        if d:
            dora_set.add(base_tile(d))

    results = []
    for i in range(len(hand_14)):
        remaining = hand_14[:i] + hand_14[i + 1:]
        sh = calculate_shanten(remaining)
        type_count, weighted_count = (
            _count_weighted_acceptance(remaining, rivers=rivers) if sh >= 0 else (0, 0)
        )
        tile = hand_14[i]
        safety_raw = _tile_safety_score(tile, rivers, player_name, riichi_players)

        w = _aw.get()
        _sh_table = {int(k): v for k, v in w["sh_table"].items()}

        # --- 1. シャンテン点 (0–50) ---
        sh_score = _sh_table.get(sh, max(0, 4 - sh))

        # --- 2. 重み付き受け入れ枚数点 ---
        acc_score = min(w["acc_max"], weighted_count / w["acc_norm"] * w["acc_max"])

        # --- 3. ドラ点 ---
        dora_kept = sum(1 for t in remaining if base_tile(t) in dora_set)
        dora_score = min(w["dora_max"], dora_kept * w["dora_per_tile"])
        if base_tile(tile) in dora_set:
            dora_score -= w["dora_discard_penalty"]

        # --- 4. 安全度点 (リーチ数に応じて重みを増加) ---
        safety_weight = w["safety_base"] + riichi_count * w["safety_per_riichi"]
        safety_score = safety_raw * safety_weight

        # --- 5. 場況補正 ---
        situation_score = 0.0
        if player_name and scores:
            my_score = scores.get(player_name, 25000)
            others = [v for k, v in scores.items() if k != player_name]
            lead = my_score - max(others) if others else 0
            is_late = wall_count <= w["late_wall"]
            is_very_late = wall_count <= w["very_late_wall"]
            if riichi_count >= 1 and is_late:
                situation_score += safety_raw / 100 * w["riichi_late_bonus"]
            if lead >= w["lead_threshold"] and is_late:
                situation_score += safety_raw / 100 * w["lead_late_bonus"]
            if is_very_late:
                situation_score += safety_raw / 100 * w["very_late_bonus"]
            if sh == 0:
                situation_score += w["tenpai_bonus"]
                # メンゼンテンパイはリーチ可能 → 追加ボーナス
                if not has_melds and scores.get(player_name, 25000) >= 1000:
                    situation_score += w["riichi_eligible_bonus"]

        raw_total = sh_score + acc_score + dora_score + safety_score + situation_score

        results.append({
            "idx": i,
            "tile": tile,
            "sh": sh,
            "acc": type_count,
            "weighted_acc": weighted_count,
            "safety_raw": safety_raw,
            "sh_score": round(sh_score, 1),
            "acc_score": round(acc_score, 1),
            "dora_score": round(dora_score, 1),
            "safety_score": round(safety_score, 1),
            "situation_score": round(situation_score, 1),
            "raw_total": round(raw_total, 1),
            "label": tile_to_label(tile),
        })

    results.sort(key=lambda c: -c["raw_total"])
    return results


def _normalize_to_100(candidates):
    """
    AI最善手を100点として、各候補を比例スコアで正規化する。
    最善手=100点、他は best_raw に対する比率で算出。
    """
    if not candidates:
        return candidates
    best_raw = candidates[0]["raw_total"]
    for c in candidates:
        if best_raw > 0:
            c["total_score"] = round(min(100.0, c["raw_total"] / best_raw * 100), 1)
        else:
            c["total_score"] = 100.0
    return candidates


def _evaluate_hand_score(hand_14, context=None):
    """
    手牌から各打牌候補を「AI最善手=100点」で正規化スコアリング。
    """
    raw = _evaluate_hand_raw(hand_14, context=context)
    return _normalize_to_100(raw)


def _tile_safety(tile, rivers, current_player):
    if not rivers or current_player is None:
        return 0
    score = 0
    for name, river in rivers.items():
        if name == current_player:
            continue
        if base_tile(tile) in [base_tile(r) for r in river]:
            score += 1
    return score


# ==========================================
# 状況適応コメント生成
# ==========================================

def _generate_situation_comment(tile, rivers, current_player, context=None):
    wall_count = context.get("wall_count", 70) if context else 70
    riichi_players = context.get("riichi", {}) if context else {}
    scores = context.get("scores") if context else None
    player_name = context.get("player", current_player) if context else current_player

    total_wall = 70
    progress = 1.0 - (wall_count / total_wall)

    bt = base_tile(tile)
    suit = bt[1] if len(bt) >= 2 else "z"
    try:
        num = int(bt[0])
    except (ValueError, IndexError):
        num = 0

    riichi_count = sum(1 for p, r in riichi_players.items() if r and p != (player_name or current_player))

    safe_count = 0
    if rivers and current_player:
        for name, river in rivers.items():
            if name == current_player:
                continue
            if bt in [base_tile(r) for r in river]:
                safe_count += 1

    parts = []
    if progress < 0.3:
        if suit == "z":
            parts.append("序盤は字牌の孤立牌から切り進めるのが基本です。")
        elif num in (1, 9):
            parts.append("序盤の老頭牌処理。手牌の流動性を上げる効果があります。")
        elif num in (4, 5, 6):
            parts.append("序盤に中央牌を切るのは受け入れを狭める可能性があるため、本当に不要か確認しましょう。")
        else:
            parts.append("序盤はまだ相手の情報も少なく、放銃リスクは限定的です。")
    elif progress < 0.6:
        if safe_count == 0:
            parts.append("中盤に差しかかり、未出の牌への注意が必要になってきます。")
        elif safe_count >= 2:
            parts.append("複数人の現物牌です。中盤でも比較的安心して打てます。")
        else:
            parts.append("中盤。1人の現物ですが、他家にはまだ見えていません。")
    else:
        if riichi_count > 0:
            if safe_count == 0:
                parts.append(f"⚠️ リーチ者が{riichi_count}人います。この牌は現物ではないため慎重な判断が必要です。")
            elif safe_count >= 2:
                parts.append(f"リーチ者が{riichi_count}人いますが、複数人の現物なので比較的安全です。")
            else:
                parts.append(f"リーチ者が{riichi_count}人いる終盤。1人の現物ですが、他家には通っていません。")
        elif safe_count == 0:
            parts.append("終盤の無筋牌です。放銃の可能性を念頭に置いて判断しましょう。")
        else:
            parts.append(f"終盤ですが、{safe_count}人の現物牌です。安心して打てます。")

    if scores and player_name:
        my = scores.get(player_name, 25000)
        others = [v for k, v in scores.items() if k != player_name]
        if others:
            lead = my - max(others)
            if lead >= 10000:
                parts.append("大きくリードしているため、無理な攻めより安全牌優先も選択肢です。")
            elif lead <= -10000:
                parts.append("大きくビハインドのため、高打点を意識した攻撃的選択が重要です。")

    if suit == "z":
        num_z = int(bt[0]) if bt[0].isdigit() else 0
        if num_z in (5, 6, 7):
            parts.append("三元牌は相手の役牌になる可能性があります。序盤から副露される危険性に注意。")
        elif num_z in (1, 2):
            parts.append("場風牌は役牌になりやすいため、副露されていたら特に注意が必要です。")

    return " ".join(parts) if parts else "打牌を行います。"


# ==========================================
# CPU最適打牌
# ==========================================

def _count_acceptance(hand_13):
    current_shanten = calculate_shanten(hand_13)
    if current_shanten < 0:
        return 0
    count = 0
    all_tiles = [f"{i}{s}" for s in "mps" for i in range(1, 10)] + [
        f"{i}z" for i in range(1, 8)
    ]
    for t in all_tiles:
        test_hand = hand_13 + [t]
        remaining_sh = calculate_shanten_14(test_hand)
        if remaining_sh < current_shanten:
            count += 1
    return count


def _should_defend(hand_14, context=None):
    """守備優先にすべき局面かを判定する。"""
    if context is None:
        return False
    wall_count = context.get("wall_count", 70)
    riichi_players = context.get("riichi", {})
    scores = context.get("scores", {})
    player_name = context.get("player")

    sh = calculate_shanten_14(hand_14)
    progress = 1.0 - (wall_count / 70)
    riichi_count = sum(1 for p, r in riichi_players.items() if r and p != player_name)

    lead = 0
    if scores and player_name:
        my = scores.get(player_name, 25000)
        others = [v for k, v in scores.items() if k != player_name]
        lead = my - max(others) if others else 0

    # 2人以上リーチ & 1シャンテン以上 → 守備
    if riichi_count >= 2 and sh >= 1:
        return True
    # 1人リーチ & 2シャンテン以上 & 中盤以降 → 守備
    if riichi_count >= 1 and sh >= 2 and progress >= 0.35:
        return True
    # 1人リーチ & 3シャンテン以上（どこからでも守備）
    if riichi_count >= 1 and sh >= 3:
        return True
    # 大幅リード & 終盤 & 1シャンテン以上 → 守備
    if lead >= 12000 and progress >= 0.6 and sh >= 2:
        return True
    return False


def find_best_discard(hand_14, context=None):
    # 守備モード: 最も安全な牌を選ぶ
    if _should_defend(hand_14, context):
        rivers = context.get("rivers") if context else None
        player_name = context.get("player") if context else None
        riichi_players = context.get("riichi", {}) if context else {}

        best_idx = 0
        best_safety = -1
        for i, tile in enumerate(hand_14):
            safety = _tile_safety_score(tile, rivers, player_name, riichi_players)
            if safety > best_safety:
                best_safety = safety
                best_idx = i
        remaining = hand_14[:best_idx] + hand_14[best_idx + 1:]
        return best_idx, calculate_shanten(remaining)

    candidates = _evaluate_hand_score(hand_14, context=context)
    if not candidates:
        return 0, 99
    best = candidates[0]
    return best["idx"], best["sh"]


def get_discard_candidates(hand_14, context=None, top_k=3):
    candidates = _evaluate_hand_score(hand_14, context=context)
    rivers = context.get("rivers") if context else None
    player_name = context.get("player") if context else None
    result = []
    for c in candidates[:top_k]:
        c["danger_description"] = _generate_situation_comment(
            c["tile"], rivers, player_name, context=context
        )
        result.append(c)
    return result


# ==========================================
# AI思考表示（狙い方・戦略判断）
# ==========================================

def analyze_ai_strategy(hand, context=None):
    """
    AIの思考・戦略を分析して人間に分かりやすく表示する。
    13枚または14枚の手牌に対応。
    何を狙っているか、攻めか降りかを判断。

    戻り値: dict
        strategy: "攻め" | "降り" | "様子見"
        target: 狙いの説明
        reasoning: 理由
    """
    rivers = context.get("rivers", {}) if context else {}
    scores = context.get("scores") if context else None
    player_name = context.get("player") if context else None
    wall_count = context.get("wall_count", 70) if context else 70
    riichi_players = context.get("riichi", {}) if context else {}

    if len(hand) >= 14:
        try:
            candidates = _evaluate_hand_raw(hand, context=context)
        except (IndexError, ValueError):
            candidates = []
        if not candidates:
            return {"strategy": "不明", "target": "—", "reasoning": "手牌を評価できませんでした。", "shanten": 99}
        best = candidates[0]
        sh = best["sh"]
        acc = best["acc"]
        hand_snap = list(hand)  # スナップショットで安全にスライス
        hand_remaining = hand_snap[:best["idx"]] + hand_snap[best["idx"] + 1:]
    else:
        # 13枚手牌（CPUの通常状態）
        sh = calculate_shanten(hand)
        acc = _count_acceptance(hand) if sh >= 0 else 0
        hand_remaining = list(hand)

    progress = 1.0 - (wall_count / 70)
    riichi_count = sum(1 for p, r in riichi_players.items() if r and p != player_name)

    # 点数差
    lead = 0
    if scores and player_name:
        my = scores.get(player_name, 25000)
        others = [v for k, v in scores.items() if k != player_name]
        lead = my - max(others) if others else 0

    # 手牌構成の分析
    suits = {"m": [], "p": [], "s": [], "z": []}
    for t in hand_remaining:
        bt = base_tile(t)
        s = bt[1]
        suits[s].append(int(bt[0]) if s != "z" else int(bt[0]))

    # 染め手判定
    suit_counts = {s: len(v) for s, v in suits.items() if s != "z"}
    total_num_tiles = sum(suit_counts.values())
    max_suit_count = max(suit_counts.values()) if suit_counts else 0
    is_honitsu_like = total_num_tiles > 0 and (max_suit_count / total_num_tiles) >= 0.85

    # 対子系判定
    from collections import Counter
    bt_counts = Counter(base_tile(t) for t in hand_remaining)
    pair_count = sum(1 for c in bt_counts.values() if c >= 2)
    is_toitsu_like = pair_count >= 4

    # 戦略判定
    strategy = "攻め"
    target_parts = []
    reasoning_parts = []

    # 降り判定（_should_defend と同じ基準を使用）
    should_defend = False
    if riichi_count >= 2 and sh >= 1:
        should_defend = True
    elif riichi_count >= 1 and sh >= 2 and progress >= 0.35:
        should_defend = True
    elif riichi_count >= 1 and sh >= 3:
        should_defend = True
    elif lead >= 12000 and progress >= 0.6 and sh >= 2:
        should_defend = True

    if should_defend:
        strategy = "降り"
        target_parts.append("安全牌を中心に現物切り")
        if riichi_count >= 2:
            reasoning_parts.append(f"リーチ者が{riichi_count}人おり、テンパイまで遠い（シャンテン{sh}）ため守備優先。")
        elif lead >= 15000:
            reasoning_parts.append(f"大きくリード中（差: {lead:+,}点）。終盤なので無理に攻めずトップ確保を狙う。")
        else:
            reasoning_parts.append(f"リーチ者がおり進行度も高いため、手の進み具合から降りが最適。")
    else:
        # 攻め判定
        if sh == 0:
            strategy = "攻め（テンパイ）"
            target_parts.append("あと1牌でアガリ")
            if acc > 0:
                target_parts.append(f"（待ち牌: {acc}種類）")
            reasoning_parts.append("テンパイ状態。ツモまたはロンでのアガリを狙っています。")
        elif sh == 1:
            strategy = "攻め（イーシャンテン）"
            target_parts.append("テンパイを目指して有効牌を引きたい")
            reasoning_parts.append(f"受け入れ{acc}種類。あと1枚でテンパイ、攻め姿勢で進めています。")
        elif sh == 2:
            if riichi_count >= 1:
                strategy = "様子見"
                target_parts.append("攻守バランスをとりつつテンパイを目指す")
                reasoning_parts.append("2シャンテンだが相手リーチもあり、安全牌を使いながら手を進めている。")
            else:
                strategy = "攻め"
                target_parts.append("面子構成を整え、イーシャンテンへ向かう")
                reasoning_parts.append(f"2シャンテン。受け入れ{acc}種類で手を広くしながら進めている。")
        else:
            strategy = "様子見" if riichi_count >= 1 else "攻め"
            target_parts.append("手牌を整理して少しずつ前進")
            reasoning_parts.append(f"{sh}シャンテン。まだ先は長いが、不要牌を処理しながら前進中。")

        # 狙い手の推測
        if is_honitsu_like:
            dominant_suit = max(suit_counts, key=suit_counts.get)
            s_name = {"m": "萬子", "p": "筒子", "s": "索子"}.get(dominant_suit, "?")
            target_parts.append(f"手牌構成から **混一色（{s_name}）** を意識している可能性があります。")
        elif is_toitsu_like:
            target_parts.append("対子が多く、**七対子** を意識している可能性があります。")
        else:
            target_parts.append("面子手（通常のメンツ構成）を進めています。")

    return {
        "strategy": strategy,
        "target": " ".join(target_parts),
        "reasoning": " ".join(reasoning_parts),
        "shanten": sh,
    }


# ==========================================
# ホバー（切る前の）プレビュー情報
# ==========================================

def get_tile_hover_info(hand_14, tile_idx, context=None):
    """
    hand_14[tile_idx] を切った場合の情報を簡潔に返す。
    """
    if tile_idx < 0 or tile_idx >= len(hand_14):
        return None

    tile = hand_14[tile_idx]
    remaining = hand_14[:tile_idx] + hand_14[tile_idx + 1:]
    sh = calculate_shanten(remaining)
    acc = _count_acceptance(remaining) if sh >= 0 else 0

    safety_raw = 30
    if context:
        rivers = context.get("rivers")
        player_name = context.get("player")
        riichi_players = context.get("riichi", {})
        safety_raw = _tile_safety_score(tile, rivers, player_name, riichi_players)

    # 待ち牌の候補（テンパイ時）
    waiting_tiles = []
    if sh == 0:
        all_tiles = [f"{i}{s}" for s in "mps" for i in range(1, 10)] + [
            f"{i}z" for i in range(1, 8)
        ]
        for t in all_tiles:
            test_hand = remaining + [t]
            if calculate_shanten_14(test_hand) == -1:
                waiting_tiles.append(tile_to_label(t))

    sh_label = {-1: "和了形", 0: "テンパイ", 1: "イーシャンテン", 2: "2シャンテン",
                3: "3シャンテン"}.get(sh, f"{sh}シャンテン")

    info = f"**{tile_to_label(tile)}** を切ると → {sh_label}"
    if sh == 0 and waiting_tiles:
        info += f"\n待ち: {', '.join(waiting_tiles[:8])}"
    elif sh >= 1:
        info += f"（受入{acc}種類）"

    if safety_raw >= 75:
        info += " | 🟢安全"
    elif safety_raw >= 50:
        info += " | 🟡やや安全"
    elif safety_raw >= 30:
        info += " | 🟠注意"
    else:
        info += " | 🔴危険"

    return info


def get_all_tile_hover_info(hand_14, context=None):
    """
    手牌の全牌分のホバー情報をリストで返す。
    """
    return [get_tile_hover_info(hand_14, i, context) for i in range(len(hand_14))]


# ==========================================
# 和了（アガリ）判定
# ==========================================

def check_tsumo(hand_14, is_riichi=False, player_wind=None, round_wind=None,
                dora_indicators=None, melds=None):
    try:
        tiles_136 = tiles_to_136(hand_14)
        win_tile_136 = tile_to_136(hand_14[-1])
        dora_136 = None
        if dora_indicators:
            dora_136 = [tile_to_136(d) for d in dora_indicators]
        meld_objs = build_melds_136(melds) if melds else None
        config = HandConfig(
            is_tsumo=True, is_riichi=is_riichi,
            player_wind=player_wind, round_wind=round_wind,
        )
        result = hand_calculator.estimate_hand_value(
            tiles_136, win_tile_136, dora_indicators=dora_136,
            config=config, melds=meld_objs,
        )
        if result.error is None and result.cost:
            return result
        return None
    except Exception:
        return None


def check_ron(hand_13, discard_tile, is_riichi=False, player_wind=None,
              round_wind=None, dora_indicators=None, melds=None):
    try:
        full_hand = hand_13 + [discard_tile]
        tiles_136 = tiles_to_136(full_hand)
        win_tile_136 = tile_to_136(discard_tile)
        dora_136 = None
        if dora_indicators:
            dora_136 = [tile_to_136(d) for d in dora_indicators]
        meld_objs = build_melds_136(melds) if melds else None
        config = HandConfig(
            is_tsumo=False, is_riichi=is_riichi,
            player_wind=player_wind, round_wind=round_wind,
        )
        result = hand_calculator.estimate_hand_value(
            tiles_136, win_tile_136, dora_indicators=dora_136,
            config=config, melds=meld_objs,
        )
        if result.error is None and result.cost:
            return result
        return None
    except Exception:
        return None


def is_tenpai(hand_13):
    return calculate_shanten(hand_13) == 0


def is_complete(hand_14):
    hand_13 = hand_14[:-1]
    sh = calculate_shanten(hand_13)
    if sh == 0:
        test_sh = calculate_shanten_14(hand_14)
        return test_sh == -1
    return False


# ==========================================
# 点数フォーマット
# ==========================================

def format_result(result, is_tsumo=True):
    if result is None or result.cost is None:
        return None
    cost = result.cost
    han = result.han
    fu = result.fu
    if isinstance(cost, dict):
        main_points = cost.get("main", 0)
        additional = cost.get("additional", 0)
    else:
        main_points = getattr(cost, "main", 0)
        additional = getattr(cost, "additional", 0)
    yaku_list = []
    if result.yaku:
        for y in result.yaku:
            yaku_list.append(str(y))
    return {
        "han": han, "fu": fu,
        "main_points": main_points, "additional_points": additional,
        "yaku": yaku_list, "is_tsumo": is_tsumo,
    }


# ==========================================
# AI評価（プレイヤーの打牌の分析）
# ==========================================

def evaluate_discard(hand_14, player_discard_idx, context=None):
    """
    プレイヤーの打牌を評価。AI最善手=100点での相対スコアで返す。
    """
    all_candidates = _evaluate_hand_score(hand_14, context=context)
    if not all_candidates:
        return {"grade": "—", "message": "評価できません。", "is_strictly_best": False}

    best = all_candidates[0]
    best_idx = best["idx"]
    best_score = best["total_score"]  # 常に100点

    player_cand = next((c for c in all_candidates if c["idx"] == player_discard_idx), None)
    if player_cand is None:
        return {"grade": "—", "message": "評価できません。", "is_strictly_best": False}

    player_score = player_cand["total_score"]
    player_sh = player_cand["sh"]
    best_sh = best["sh"]
    diff_sh = player_sh - best_sh

    player_tile = hand_14[player_discard_idx]
    best_tile = hand_14[best_idx]
    player_tile_label = tile_to_label(player_tile)
    best_tile_label = tile_to_label(best_tile)

    is_strictly_best = (player_discard_idx == best_idx)

    rivers = context.get("rivers") if context else None
    player_name = context.get("player") if context else "Player"

    player_situation = _generate_situation_comment(player_tile, rivers, player_name, context=context)
    best_situation = _generate_situation_comment(best_tile, rivers, player_name, context=context)

    # グレード判定（比例スコアベース: AI最善=100点）
    if is_strictly_best:
        grade = "🏆 最善手"
    elif player_score >= 95:
        grade = "✅ 良い手"
    elif player_score >= 85:
        grade = "👍 悪くない"
    elif player_score >= 70:
        grade = "⚠️ 改善の余地あり"
    else:
        grade = "❌ 悪手"

    # メッセージ
    if is_strictly_best:
        message = (
            f"AIも **[{player_tile_label}]** を選びます（スコア: **100/100点**）。\n\n"
            f"**状況分析:** {player_situation}"
        )
    elif diff_sh == 0:
        message = (
            f"シャンテン数は同じ（{player_sh}）ですが、スコアでは差があります。\n\n"
            f"- あなた: **[{player_tile_label}]** — **{player_score:.0f}/100点** — {player_situation}\n\n"
            f"- AI推奨: **[{best_tile_label}]** — **100/100点** — {best_situation}"
        )
    else:
        message = (
            f"あなたの選択 **[{player_tile_label}]**（**{player_score:.0f}/100点**）は、"
            f"AI推奨 **[{best_tile_label}]**（**100/100点**）より\n"
            f"シャンテンで{diff_sh}手分遅れています。\n\n"
            f"**あなたの打牌分析:** {player_situation}\n\n"
            f"**AI推奨打牌分析:** {best_situation}"
        )

    # AI思考
    ai_thinking = analyze_ai_strategy(hand_14, context=context)

    # Top3候補
    top_candidates = []
    for c in all_candidates[:3]:
        c_copy = dict(c)
        c_copy["danger_description"] = _generate_situation_comment(
            c["tile"], rivers, player_name, context=context
        )
        top_candidates.append(c_copy)

    return {
        "grade": grade,
        "message": message,
        "player_tile": player_tile,
        "player_shanten": player_sh,
        "best_tile": best_tile,
        "best_shanten": best_sh,
        "is_optimal": (diff_sh == 0),
        "is_strictly_best": is_strictly_best,
        "player_tile_label": player_tile_label,
        "best_tile_label": best_tile_label,
        "player_acceptance": player_cand["acc"],
        "best_acceptance": best["acc"],
        "total_score": player_score,
        "best_score": best_score,
        "top_candidates": top_candidates,
        "ai_thinking": ai_thinking,
        # コンポーネントスコア（ロギング・チューニング用）
        "player_sh_score": player_cand.get("sh_score", 0),
        "player_acc_score": player_cand.get("acc_score", 0),
        "player_dora_score": player_cand.get("dora_score", 0),
        "player_safety_score": player_cand.get("safety_score", 0),
        "player_situation_score": player_cand.get("situation_score", 0),
        "player_raw_total": player_cand.get("raw_total", 0),
        "best_raw_total": best.get("raw_total", 0),
        "all_candidates": all_candidates,  # 全候補（ログ用、最大14件）
    }
