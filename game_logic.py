"""
麻雀ゲームロジック
- シャンテン計算によるCPU最適打牌
- 和了（ツモ・ロン）判定
- 点数計算
"""

from mahjong.shanten import Shanten
from mahjong.tile import TilesConverter
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig

shanten_calculator = Shanten()
hand_calculator = HandCalculator()


# ==========================================
# 牌形式の変換ユーティリティ
# ==========================================

def tiles_to_string_parts(tiles):
    """
    内部形式 ['1m','2m','5p',...] を mahjong ライブラリ形式の文字列パーツに変換。
    戻り値: (man, pin, sou, honors) の文字列タプル
    """
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
    """内部形式を34配列に変換（シャンテン計算用、13枚）"""
    man, pin, sou, honors = tiles_to_string_parts(tiles)
    return TilesConverter.string_to_34_array(
        man=man or "", pin=pin or "", sou=sou or "", honors=honors or ""
    )


def tiles_to_136(tiles):
    """内部形式を136配列に変換（点数計算用、14枚）"""
    man, pin, sou, honors = tiles_to_string_parts(tiles)
    return TilesConverter.string_to_136_array(
        man=man or "", pin=pin or "", sou=sou or "", honors=honors or ""
    )


def tile_to_136(tile):
    """1枚の牌を136形式に変換"""
    base = tile.replace("r", "")
    num, suit = base[0], base[1]
    mapping = {"m": "man", "p": "pin", "s": "sou", "z": "honors"}
    kwargs = {mapping[suit]: num}
    # 他の引数はデフォルト空文字に
    for key in ["man", "pin", "sou", "honors"]:
        if key not in kwargs:
            kwargs[key] = ""
    return TilesConverter.string_to_136_array(**kwargs)[0]


# ==========================================
# 牌の正式表記（日本語）への変換
# ==========================================

KANJI_NUM = {
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}

SUIT_KANJI = {
    "m": "萬",
    "p": "筒",
    "s": "索",
}

HONOR_LABELS = {
    "1z": "東",
    "2z": "南",
    "3z": "西",
    "4z": "北",
    "5z": "白",
    "6z": "發",
    "7z": "中",
}


def tile_to_label(tile):
    """
    '2p', '5mr', '1z' など内部表記を
    '二筒', '赤五萬', '東' のような正式表記に変換する。
    """
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
    """
    13枚の手牌のシャンテン数を計算。
    戻り値: int (-1=和了, 0=テンパイ, 1=イーシャンテン, ...)
    """
    try:
        tiles_34 = tiles_to_34(hand_13)
        return shanten_calculator.calculate_shanten(tiles_34)
    except Exception:
        return 99  # エラー時は大きな値


def calculate_shanten_14(hand_14):
    """
    14枚の手牌のシャンテン数を計算。
    各牌を1枚ずつ抜いて、最もシャンテン数が小さくなるケースを返す。
    戻り値: int (-1=和了, 0=テンパイ, ...)
    """
    min_shanten = 99
    for i in range(len(hand_14)):
        remaining = hand_14[:i] + hand_14[i + 1:]
        sh = calculate_shanten(remaining)
        if sh < min_shanten:
            min_shanten = sh
    return min_shanten


# ==========================================
# CPU最適打牌（半荘を勝つことを意識した簡易評価）
# ==========================================

def _tile_safety(tile, rivers, current_player):
    """
    簡易的な安全度スコア。
    - 各対戦相手に対して、その河に同じ牌が出ていれば +1 （現物）
    """
    if not rivers or current_player is None:
        return 0

    score = 0
    for name, river in rivers.items():
        if name == current_player:
            continue
        if tile in river:
            score += 1
    return score


def get_danger_description(tile, rivers, current_player):
    """
    他家の河を参照し、この牌を切ったときの危険度の説明文を返す。
    - 現物（誰かの河に出ている）→ その人数分「比較的安全」
    - 誰の河にも出ていない → 「他家にまだ見えていない牌なので放銃リスクあり」
    """
    if not rivers or current_player is None:
        return ""

    safe_count = 0
    for name, river in rivers.items():
        if name == current_player:
            continue
        if tile in river:
            safe_count += 1

    if safe_count == 0:
        return "他家の誰の河にも出ておらず、現物ではありません。まだ他家が持っている可能性があるため、放銃のリスクがあります。"
    if safe_count == 1:
        return "1人の河に現物があります。残り2家には見えていない牌なので、やや注意が必要です。"
    if safe_count == 2:
        return "2人の河に現物があります。比較的安全な打牌です。"
    return "3人全員の河に現物があり、最も安全な打牌です。"


def _count_acceptance(hand_13):
    """
    13枚の手牌に対し、加えることでシャンテン数が下がる牌の種類数を数える。
    （受け入れ枚数 = 有効牌の種類数）
    """
    current_shanten = calculate_shanten(hand_13)
    if current_shanten < 0:
        # すでに和了形の場合は「これ以上良くならない」ので 0 扱い
        return 0

    count = 0
    # 全種類の牌を試す
    all_tiles = [f"{i}{s}" for s in "mps" for i in range(1, 10)] + [
        f"{i}z" for i in range(1, 8)
    ]

    for tile in all_tiles:
        test_hand = hand_13 + [tile]
        # 14枚でシャンテン計算（1枚抜く形ではなく、追加後の13枚ペアで見る）
        remaining_sh = calculate_shanten_14(test_hand)
        if remaining_sh < current_shanten:
            count += 1

    return count


def find_best_discard(hand_14, context=None):
    """
    14枚の手牌から、捨てる牌のインデックスを返す。

    - 基本方針: シャンテン数が最も良くなる打牌を優先
    - 同シャンテンの場合:
        - 有効牌の種類数が多い方を優先（攻撃寄り）
        - コンテキストがある場合は安全度も考慮し、
          点棒リード時 + 局の後半では 1シャンテン遅れまで許容して守備寄りに

    Args:
        hand_14: 手牌（14枚）
        context: 以下を含む辞書（任意）
            - "rivers": 各プレイヤーの河 {name: [tiles]}
            - "scores": 各プレイヤーの点数 {name: score}
            - "player": この打牌を行うプレイヤー名 (str)
            - "wall_count": 残り山枚数 (int)

    戻り値:
        (best_index, shanten_after_discard)
    """
    rivers = context.get("rivers") if context else None
    scores = context.get("scores") if context else None
    player_name = context.get("player") if context else None
    wall_count = context.get("wall_count") if context else None

    candidates = []
    for i in range(len(hand_14)):
        remaining = hand_14[:i] + hand_14[i + 1:]
        sh = calculate_shanten(remaining)
        acc = _count_acceptance(remaining) if sh >= 0 else 0
        tile = hand_14[i]
        safety = _tile_safety(tile, rivers, player_name)

        candidates.append(
            {
                "idx": i,
                "sh": sh,
                "acc": acc,
                "safety": safety,
            }
        )

    if not candidates:
        return 0, 99

    # まず最良シャンテン
    min_shanten = min(c["sh"] for c in candidates)

    # コンテキストが無い場合は従来通り:
    # 最良シャンテン → 有効牌種類数 で決定
    if context is None:
        best = None
        best_acceptance = -1
        for c in candidates:
            if c["sh"] != min_shanten:
                continue
            if c["acc"] > best_acceptance:
                best_acceptance = c["acc"]
                best = c
        if best is None:
            best = candidates[-1]
        return best["idx"], best["sh"]

    # 半荘全体を意識した簡易パラメータ
    slack = 0  # 最良シャンテンからどこまで許容するか
    if scores is not None and player_name is not None and wall_count is not None:
        my_score = scores.get(player_name, 25000)
        others = [v for k, v in scores.items() if k != player_name]
        lead = my_score - max(others) if others else 0
        is_late = wall_count <= 20  # 終盤かどうか（かなり大雑把）

        # 大きくリードしていて終盤なら、1シャンテン遅れまで守備寄りを許容
        if lead >= 8000 and is_late:
            slack = 1

    # 許容範囲内の候補のみを残す
    allowed = [c for c in candidates if c["sh"] <= min_shanten + slack]
    if not allowed:
        allowed = candidates

    # 攻守バランスのスコア：
    #   - シャンテンの良さを最優先（小さいほど良いのでマイナス）
    #   - 受け入れ（acc）は将来の上がりやすさ
    #   - safety は放銃率を下げる要素
    best = None
    best_score = -10**9
    for c in allowed:
        score = (
            -c["sh"] * 100  # シャンテン優先
            + c["acc"] * 2  # 受け入れ
            + c["safety"] * 20  # 安全度
        )
        if score > best_score:
            best_score = score
            best = c

    if best is None:
        best = allowed[-1]
    return best["idx"], best["sh"]


def get_discard_candidates(hand_14, context=None, top_k=3):
    """
    打牌候補を評価スコア順に最大 top_k 件返す。
    戻り値: list of dict
        idx, tile, shanten, acceptance, safety, danger_description, label
    """
    rivers = context.get("rivers") if context else None
    scores = context.get("scores") if context else None
    player_name = context.get("player") if context else None
    wall_count = context.get("wall_count") if context else None

    candidates = []
    for i in range(len(hand_14)):
        remaining = hand_14[:i] + hand_14[i + 1:]
        sh = calculate_shanten(remaining)
        acc = _count_acceptance(remaining) if sh >= 0 else 0
        tile = hand_14[i]
        safety = _tile_safety(tile, rivers, player_name)
        danger_desc = get_danger_description(tile, rivers, player_name)

        # スコア計算（find_best_discard と同様）
        score = -sh * 100 + acc * 2 + safety * 20
        if context and scores and player_name is not None and wall_count is not None:
            my_score = scores.get(player_name, 25000)
            others = [v for k, v in scores.items() if k != player_name]
            lead = my_score - max(others) if others else 0
            is_late = wall_count <= 20
            if lead >= 8000 and is_late:
                pass  # slack はソートには使わず、スコアで一括
        candidates.append({
            "idx": i,
            "tile": tile,
            "sh": sh,
            "acc": acc,
            "safety": safety,
            "danger_description": danger_desc,
            "label": tile_to_label(tile),
            "score": score,
        })

    candidates.sort(key=lambda c: (-c["score"], -c["acc"]))
    return candidates[:top_k]


# ==========================================
# 和了（アガリ）判定
# ==========================================

def check_tsumo(hand_14, is_riichi=False, player_wind=None, round_wind=None, dora_indicators=None):
    """
    ツモ和了の判定と点数計算。
    
    Args:
        hand_14: 14枚の手牌
        is_riichi: リーチ状態か
        player_wind: 自風 (None, 27=東, 28=南, 29=西, 30=北)
        round_wind: 場風
        dora_indicators: ドラ表示牌のリスト（内部形式）
    
    戻り値: HandResponse or None
    """
    try:
        tiles_136 = tiles_to_136(hand_14)
        win_tile_136 = tile_to_136(hand_14[-1])  # 最後にツモった牌
        
        # ドラ表示牌の変換
        dora_136 = None
        if dora_indicators:
            dora_136 = [tile_to_136(d) for d in dora_indicators]
        
        config = HandConfig(
            is_tsumo=True,
            is_riichi=is_riichi,
            player_wind=player_wind,
            round_wind=round_wind,
        )
        
        result = hand_calculator.estimate_hand_value(
            tiles_136, 
            win_tile_136, 
            dora_indicators=dora_136,
            config=config
        )
        
        if result.error is None and result.cost:
            return result
        return None
    except Exception:
        return None


def check_ron(hand_13, discard_tile, is_riichi=False, player_wind=None, round_wind=None, dora_indicators=None):
    """
    ロン和了の判定と点数計算。
    
    Args:
        hand_13: 13枚の手牌
        discard_tile: 他家の捨て牌
        is_riichi: リーチ状態か
        player_wind: 自風
        round_wind: 場風
        dora_indicators: ドラ表示牌のリスト
    
    戻り値: HandResponse or None
    """
    try:
        full_hand = hand_13 + [discard_tile]
        tiles_136 = tiles_to_136(full_hand)
        win_tile_136 = tile_to_136(discard_tile)
        
        dora_136 = None
        if dora_indicators:
            dora_136 = [tile_to_136(d) for d in dora_indicators]
        
        config = HandConfig(
            is_tsumo=False,
            is_riichi=is_riichi,
            player_wind=player_wind,
            round_wind=round_wind,
        )
        
        result = hand_calculator.estimate_hand_value(
            tiles_136, 
            win_tile_136,
            dora_indicators=dora_136,
            config=config,
        )
        
        if result.error is None and result.cost:
            return result
        return None
    except Exception:
        return None


def is_tenpai(hand_13):
    """テンパイ判定（シャンテン数 == 0）"""
    return calculate_shanten(hand_13) == 0


def is_complete(hand_14):
    """和了形かどうかの判定（シャンテン数 == -1）"""
    # 14枚を13枚+1枚として扱う
    hand_13 = hand_14[:-1]
    sh = calculate_shanten(hand_13)
    # テンパイ（0）の時にツモ牌で和了できるか確認
    if sh == 0:
        # 全体でシャンテン計算
        test_sh = calculate_shanten_14(hand_14)
        return test_sh == -1
    return False


# ==========================================
# 点数フォーマット
# ==========================================

def format_result(result, is_tsumo=True):
    """HandResponseを人間が読める形式に変換"""
    if result is None or result.cost is None:
        return None
    
    cost = result.cost
    han = result.han
    fu = result.fu
    
    # costはdict型
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
        "han": han,
        "fu": fu,
        "main_points": main_points,
        "additional_points": additional,
        "yaku": yaku_list,
        "is_tsumo": is_tsumo,
    }


# ==========================================
# AI評価（プレイヤーの打牌の分析）
# ==========================================

def evaluate_discard(hand_14, player_discard_idx, context=None):
    """
    プレイヤーの打牌を評価し、最適手との比較を返す。
    他家の河を参照した危険度の説明を含む。
    
    戻り値: dict
        is_strictly_best: 最善手（AIと同一打牌）のときのみ True（厳格カウント用）
    """
    best_idx, best_shanten = find_best_discard(hand_14, context=context)
    top_candidates = get_discard_candidates(hand_14, context=context, top_k=3)

    player_remaining = hand_14[:player_discard_idx] + hand_14[player_discard_idx + 1:]
    player_shanten = calculate_shanten(player_remaining)
    player_tile = hand_14[player_discard_idx]
    best_tile = hand_14[best_idx]

    player_accept = _count_acceptance(player_remaining) if player_shanten >= 0 else 0
    best_remaining = hand_14[:best_idx] + hand_14[best_idx + 1:]
    best_accept = _count_acceptance(best_remaining) if best_shanten >= 0 else 0

    is_optimal = (player_shanten == best_shanten)
    is_strictly_best = (player_discard_idx == best_idx)

    player_tile_label = tile_to_label(player_tile)
    best_tile_label = tile_to_label(best_tile)

    rivers = context.get("rivers") if context else None
    player_danger = get_danger_description(player_tile, rivers, "Player")
    best_danger = get_danger_description(best_tile, rivers, "Player")

    # 判定メッセージ（攻めの評価 + 守り・危険度の説明）
    if is_strictly_best:
        grade = "🏆 最善手"
        message = (
            f"AIも [{player_tile_label}] を選びます。\n"
            f"シャンテン数は {player_shanten}、有効牌の種類はおおよそ {player_accept} 種類です。\n"
        )
        if best_danger:
            message += f"**安全度:** {best_danger}\n"
        message += "上がりまでの距離と受け入れのバランスが良く、かつ安全面も考慮された選択です。"
    elif is_optimal:
        grade = "✅ 良い手"
        message = (
            f"シャンテン数は同じ（{player_shanten}）ですが、\n"
            f"AIは [{best_tile_label}] を切ることで有効牌の種類を {best_accept} 種類にしています。\n"
            f"あなたの [{player_tile_label}] も悪くはありませんが、受け入れの観点では [{best_tile_label}] が上です。\n"
        )
        if player_danger:
            message += f"**あなたの打牌の安全度:** {player_danger}\n"
        if best_danger and player_tile != best_tile:
            message += f"**AI推奨牌の安全度:** {best_danger}"
    else:
        diff = player_shanten - best_shanten
        grade = "⚠️ 改善の余地あり" if diff == 1 else "❌ 悪手"
        message = (
            f"あなたの選択: [{player_tile_label}] → シャンテン数 {player_shanten}、有効牌約 {player_accept} 種類。\n"
            f"AI推奨: [{best_tile_label}] → シャンテン数 {best_shanten}、有効牌約 {best_accept} 種類。\n"
            f"シャンテンで {diff} 手分遅れており、上がりまでの道が細くなっています。\n"
        )
        if player_danger:
            message += f"**あなたの打牌の安全度:** {player_danger}\n"
        if best_danger:
            message += f"**推奨牌の安全度:** {best_danger}\n"
        message += "早いテンパイと受け入れの広さを意識すると、半荘で安定してトップを取りやすくなります。"

    return {
        "grade": grade,
        "message": message,
        "player_tile": player_tile,
        "player_shanten": player_shanten,
        "best_tile": best_tile,
        "best_shanten": best_shanten,
        "is_optimal": is_optimal,
        "is_strictly_best": is_strictly_best,
        "player_tile_label": player_tile_label,
        "best_tile_label": best_tile_label,
        "player_acceptance": player_accept,
        "best_acceptance": best_accept,
        "top_candidates": top_candidates,
    }
