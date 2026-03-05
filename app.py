import streamlit as st
import random
import time
import base64
from pathlib import Path

from st_clickable_images import clickable_images
from game_logic import (
    find_best_discard,
    check_tsumo,
    check_ron,
    is_tenpai,
    is_complete,
    calculate_shanten,
    calculate_shanten_14,
    format_result,
    evaluate_discard,
    tile_to_label,
    get_discard_candidates,
)


# ==========================================
# 0. 画像パスとBase64変換 (赤ドラ対応)
# ==========================================
def get_tile_b64(tile_id, style="0"):
    is_red = tile_id.endswith("r")
    base_id = tile_id.replace("r", "")
    num, suit = base_id[0], base_id[1]
    s = {"m": "m", "p": "p", "s": "s", "z": "j"}.get(suit, suit)

    if is_red:
        red_map = {
            "3m": "c",
            "5m": "e",
            "3p": "c",
            "5p": "e",
            "1s": "a",
            "3s": "c",
            "5s": "e",
            "7s": "g",
        }
        num = red_map.get(base_id, num)

    filename = f"{style}{s}{num}.png"
    img_path = Path(__file__).parent.absolute() / "images" / filename

    if img_path.exists():
        with open(img_path, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    return ""


# ==========================================
# 0.5 理牌ソートキー
# ==========================================
def tile_sort_key(tile):
    """萬子(m) -> 筒子(p) -> 索子(s) -> 字牌(z)、数字昇順"""
    base = tile.replace("r", "")
    num_str = base[0]
    suit = base[1]
    suit_order = {"m": 0, "p": 1, "s": 2, "z": 3}
    s_val = suit_order.get(suit, 99)
    try:
        n_val = int(num_str)
    except ValueError:
        n_val = 0
    is_red = 1 if "r" in tile else 0
    return (s_val, n_val, is_red)


# ==========================================
# 1. 状態管理
# ==========================================
PLAYER_ORDER = ["Player", "CPU1", "CPU2", "CPU3"]

WIND_LABEL = {
    27: "東",
    28: "南",
    29: "西",
    30: "北",
}


def get_round_info(round_number: int):
    """
    round_number から 場風・何局目か を求める。
    1-4: 東1〜4局, 5-8: 南1〜4局（9以降は南4として表示）
    """
    idx = max(0, round_number - 1)
    if idx >= 8:
        idx = 7
    stage = "東" if idx < 4 else "南"
    kyoku = (idx % 4) + 1
    round_wind = 27 if stage == "東" else 28
    return {
        "stage_label": stage,
        "kyoku": kyoku,
        "round_wind": round_wind,
        "label": f"{stage}{kyoku}局",
    }


def get_dealer(round_number: int) -> str:
    """単純化した親の位置: round_number に応じて 4人が順番に親になる。"""
    idx = (max(0, round_number - 1)) % 4
    return PLAYER_ORDER[idx]


def get_player_winds(round_number: int):
    """
    局ごとの自風を計算する。
    - 親（東家）は get_dealer(round_number)
    - 以降、反時計回りに 南・西・北
    """
    dealer = get_dealer(round_number)
    dealer_idx = PLAYER_ORDER.index(dealer)
    winds = {}
    wind_vals = [27, 28, 29, 30]  # 東, 南, 西, 北
    for offset in range(4):
        p = PLAYER_ORDER[(dealer_idx + offset) % 4]
        winds[p] = wind_vals[offset]
    return winds


def apply_tsumo_points(winner: str, fmt: dict):
    """
    mahjong ライブラリの main/additional を使ってツモ時の点数移動を行う。
    - winner が親: 全員から additional
    - winner が子: 親から main、残り2人から additional
    """
    main = fmt["main_points"]
    add = fmt["additional_points"]
    dealer = get_dealer(GS["round_number"])

    for p in PLAYER_ORDER:
        if p == winner:
            continue
        if winner == dealer:
            # 親ツモ: 全員一律 additional
            GS["scores"][p] -= add
            GS["scores"][winner] += add
        else:
            # 子ツモ: 親から main、その他から additional
            if p == dealer:
                GS["scores"][p] -= main
                GS["scores"][winner] += main
            else:
                GS["scores"][p] -= add
                GS["scores"][winner] += add

if "gs" not in st.session_state:
    wall = [f"{i}{s}" for s in "mps" for i in range(1, 10) for _ in range(4)] + [
        f"{i}z" for i in range(1, 8) for _ in range(4)
    ]
    random.shuffle(wall)

    dora_indicator = wall[-10]

    hands = {
        p: sorted([wall.pop() for _ in range(13)], key=tile_sort_key)
        for p in PLAYER_ORDER
    }
    hands["Player"].append(wall.pop())  # 14枚スタート

    st.session_state.gs = {
        "wall": wall,
        "hands": hands,
        "rivers": {p: [] for p in PLAYER_ORDER},
        "turn": "Player",
        "has_drawn": True,
        "turn_count": 0,
        "dora_indicator": dora_indicator,
        "game_over": False,
        "game_result": None,
        "eval_history": [],
        "last_eval": None,
        "scores": {p: 25000 for p in PLAYER_ORDER},
        "riichi": {p: False for p in PLAYER_ORDER},
        "riichi_pot": 0,
        "round_number": 1,
        "riichi_pending": False,
    }

GS = st.session_state.gs

# 既存セッションに不足キーがあれば補完
if "round_number" not in GS:
    GS["round_number"] = 1
if "riichi" not in GS:
    GS["riichi"] = {p: False for p in PLAYER_ORDER}
if "riichi_pot" not in GS:
    GS["riichi_pot"] = 0
if "riichi_pending" not in GS:
    GS["riichi_pending"] = False


def start_new_round():
    """次の局を開始（点数・局数は維持、山・手牌・河・リーチ等は初期化）"""
    scores = GS["scores"]
    round_number = GS["round_number"] + 1
    wall = [f"{i}{s}" for s in "mps" for i in range(1, 10) for _ in range(4)] + [
        f"{i}z" for i in range(1, 8) for _ in range(4)
    ]
    random.shuffle(wall)
    dora_indicator = wall[-10]
    hands = {
        p: sorted([wall.pop() for _ in range(13)], key=tile_sort_key)
        for p in PLAYER_ORDER
    }
    hands["Player"].append(wall.pop())
    GS["wall"] = wall
    GS["hands"] = hands
    GS["rivers"] = {p: [] for p in PLAYER_ORDER}
    GS["turn"] = "Player"
    GS["has_drawn"] = True
    GS["turn_count"] = 0
    GS["dora_indicator"] = dora_indicator
    GS["game_over"] = False
    GS["game_result"] = None
    GS["ron_available"] = None
    GS["riichi"] = {p: False for p in PLAYER_ORDER}
    GS["riichi_pot"] = 0
    GS["riichi_pending"] = False
    GS["round_number"] = round_number
    if "tsumo_available" in GS:
        del GS["tsumo_available"]


# ==========================================
# ゲームアクション
# ==========================================
def handle_player_discard(idx):
    """プレイヤーの打牌処理 + AI評価"""
    if len(GS["hands"]["Player"]) < 14 or GS["game_over"]:
        return

    hand_before = list(GS["hands"]["Player"])  # コピー（評価用）

    # AI評価（リーチ後は打牌選択の余地がないので評価しない）
    is_player_riichi = GS["riichi"]["Player"]
    if not is_player_riichi:
        context = {
            "rivers": GS["rivers"],
            "scores": GS["scores"],
            "player": "Player",
            "wall_count": len(GS["wall"]),
        }
        eval_result = evaluate_discard(hand_before, idx, context=context)
        GS["last_eval"] = eval_result
        GS["eval_history"].append(eval_result)

    # 打牌処理
    tile = GS["hands"]["Player"].pop(idx)
    GS["rivers"]["Player"].append(tile)
    GS["hands"]["Player"].sort(key=tile_sort_key)

    # ロン判定（CPUが当たれるか）
    winds = get_player_winds(GS["round_number"])
    for cp in ["CPU1", "CPU2", "CPU3"]:
        ron_result = check_ron(
            GS["hands"][cp],
            tile,
            is_riichi=GS["riichi"][cp],
            player_wind=winds[cp],
            round_wind=get_round_info(GS["round_number"])["round_wind"],
            dora_indicators=[GS["dora_indicator"]],
        )
        if ron_result:
            fmt = format_result(ron_result, is_tsumo=False)
            winner_hand = list(GS["hands"][cp]) + [tile]
            GS["game_over"] = True
            GS["game_result"] = {
                "type": "ron",
                "winner": cp,
                "loser": "Player",
                "tile": tile,
                "details": fmt,
                "winner_hand": winner_hand,
            }
            points = fmt["main_points"]
            GS["scores"]["Player"] -= points
            GS["scores"][cp] += points
            if GS["riichi_pot"] > 0:
                GS["scores"][cp] += GS["riichi_pot"]
                GS["riichi_pot"] = 0
            st.rerun()
            return

    # この打牌でリーチ宣言が確定した場合はここでリーチ状態に移行
    if GS.get("riichi_pending"):
        # 供託はここで支払う（シャンテン0を満たした打牌のみリーチ確定）
        GS["scores"]["Player"] -= 1000
        GS["riichi_pot"] += 1000
        GS["riichi"]["Player"] = True
        GS["riichi_pending"] = False

    GS["turn"] = "CPU1"
    GS["has_drawn"] = False
    GS["turn_count"] += 1
    st.rerun()


def handle_cpu_turn(cp):
    """CPU の打牌処理（シャンテン + 受け入れ + 簡易守備のバランス）"""
    if GS["game_over"]:
        return

    # ツモ判定
    winds = get_player_winds(GS["round_number"])
    tsumo_result = check_tsumo(
        GS["hands"][cp],
        is_riichi=GS["riichi"][cp],
        player_wind=winds[cp],
        round_wind=get_round_info(GS["round_number"])["round_wind"],
        dora_indicators=[GS["dora_indicator"]],
    )
    if tsumo_result:
        fmt = format_result(tsumo_result, is_tsumo=True)
        winner_hand = list(GS["hands"][cp])
        GS["game_over"] = True
        GS["game_result"] = {
            "type": "tsumo",
            "winner": cp,
            "tile": GS["hands"][cp][-1],
            "details": fmt,
            "winner_hand": winner_hand,
        }
        apply_tsumo_points(cp, fmt)
        if GS["riichi_pot"] > 0:
            GS["scores"][cp] += GS["riichi_pot"]
            GS["riichi_pot"] = 0
        st.rerun()
        return

    # 最適打牌（半荘全体を意識した簡易評価）
    context = {
        "rivers": GS["rivers"],
        "scores": GS["scores"],
        "player": cp,
        "wall_count": len(GS["wall"]),
    }
    best_idx, _ = find_best_discard(GS["hands"][cp], context=context)
    tile = GS["hands"][cp].pop(best_idx)
    GS["rivers"][cp].append(tile)
    GS["hands"][cp].sort(key=tile_sort_key)

    # プレイヤーのロン判定
    ron_result = check_ron(
        GS["hands"]["Player"],
        tile,
        is_riichi=GS["riichi"]["Player"],
        player_wind=winds["Player"],
        round_wind=get_round_info(GS["round_number"])["round_wind"],
        dora_indicators=[GS["dora_indicator"]],
    )
    if ron_result:
        # ロン可能フラグを立てる（プレイヤーに選択させる）
        GS["ron_available"] = {
            "from": cp,
            "tile": tile,
            "result": ron_result,
        }

    # 次のプレイヤーへ
    current_idx = PLAYER_ORDER.index(cp)
    next_player = PLAYER_ORDER[(current_idx + 1) % 4]
    GS["turn"] = next_player
    GS["has_drawn"] = False

    if next_player == "Player":
        GS["turn_count"] += 1

    st.rerun()


# ==========================================
# 2. UI 表示
# ==========================================
st.set_page_config(layout="wide")

# --- サイドバー ---
st.sidebar.title("🀄 メニュー")
auto_play = st.sidebar.checkbox("⚡ 高速オートプレイ", value=False)
show_ai_eval = st.sidebar.checkbox("🤖 AI評価を表示", value=True)
st.sidebar.button("🔄 リセット", on_click=lambda: st.session_state.clear())

# --- 点数表示 ---
st.sidebar.markdown("---")
st.sidebar.markdown("### 💰 点数")
for p in PLAYER_ORDER:
    emoji = "🧑" if p == "Player" else "🤖"
    st.sidebar.markdown(f"{emoji} **{p}**: {GS['scores'][p]:,} 点")

# --- AI評価サマリー（厳格：AIと同一打牌のみ最善手） ---
if GS["eval_history"] and show_ai_eval:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📊 AI評価サマリー")
    strictly_best = sum(1 for e in GS["eval_history"] if e.get("is_strictly_best", False))
    total = len(GS["eval_history"])
    rate = (strictly_best / total * 100) if total > 0 else 0
    st.sidebar.markdown(f"最善手率: **{rate:.0f}%** ({strictly_best}/{total})")

# --- 場・局・風表示 ---
round_info = get_round_info(GS["round_number"])
st.sidebar.markdown("---")
st.sidebar.markdown(f"**{round_info['label']}（場風: {round_info['stage_label']}）**")
st.sidebar.markdown("**自風**")
_winds = get_player_winds(GS["round_number"])
for p in PLAYER_ORDER:
    seat = WIND_LABEL[_winds[p]]
    emoji = "🧑" if p == "Player" else "🤖"
    st.sidebar.caption(f"{emoji} {p}: {seat}家")

# --- ゲームオーバー / 流局 表示（次の局へ進める）---
if GS["game_over"] and GS["game_result"]:
    result = GS["game_result"]
    is_draw = result.get("type") == "exhaustive_draw"

    if is_draw:
        st.warning("🏁 **流局** - 山札がなくなりました。")
    else:
        details = result["details"]
        if result["type"] == "tsumo":
            st.success(f"🎉 **ツモ！** {result['winner']} がアガりました！")
        else:
            st.error(f"🔥 **ロン！** {result['winner']} が {result['loser']} からアガりました！")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**和了牌**: {tile_to_label(result['tile'])}")
            st.image(get_tile_b64(result["tile"], "0"), width=50)
        with col2:
            st.markdown(f"**{details['han']}翻 {details['fu']}符**")
            st.markdown(f"**{details['main_points']:,}点**")
            if details["yaku"]:
                st.markdown("**役:**")
                for y in details["yaku"]:
                    st.markdown(f"  - {y}")

        # 上がり手の最終手牌（14枚）
        if "winner_hand" in result and result["winner_hand"]:
            st.markdown("---")
            st.markdown(f"**{result['winner']} の上がり手（最終手牌）**")
            win_hand = sorted(result["winner_hand"], key=tile_sort_key)
            hand_labels = "　".join(tile_to_label(t) for t in win_hand)
            st.markdown(hand_labels)
            hand_imgs = "".join(
                f'<img src="{get_tile_b64(t, "0")}" style="height:44px; margin:1px;">' for t in win_hand
            )
            st.markdown(
                f'<div style="display:flex; flex-wrap:wrap;">{hand_imgs}</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")
    # 南4局終了後はゲーム終了、それ以外は次の局へ
    if GS["round_number"] >= 8:
        st.markdown("🏁 半荘終了（リセットで新しい半荘を開始できます）")
    else:
        if st.button("▶ 次の局へ", type="primary"):
            start_new_round()
            st.rerun()
    st.stop()

# --- 対局ボード ---
round_info = get_round_info(GS["round_number"])
st.write(
    f"### 🀄 対局ボード — {round_info['label']} (残り山: {len(GS['wall'])}枚)"
)

# ドラ表示
dora_b64 = get_tile_b64(GS["dora_indicator"], "1")
if dora_b64:
    st.markdown(
        f'<div style="display:flex; align-items:center; gap:8px;">'
        f'<span style="font-weight:bold;">ドラ表示牌:</span>'
        f'<img src="{dora_b64}" style="height:40px; border-radius:3px;">'
        f'</div>',
        unsafe_allow_html=True,
    )

# --- 河 (River) 表示 ---
cols = st.columns(4)
for i, p in enumerate(PLAYER_ORDER):
    with cols[i]:
        st.caption(f"**{p}**" + (" 👈" if p == GS["turn"] else ""))
        river_html = "".join(
            [
                f'<img src="{get_tile_b64(t, "1")}" style="height:35px; margin:1px;">'
                for t in GS["rivers"][p]
            ]
        )
        st.markdown(
            f'<div style="background:#1e272e; padding:5px; border-radius:5px; '
            f'height:160px; overflow-y:auto; display:flex; flex-wrap:wrap; '
            f'align-content:flex-start;">{river_html}</div>',
            unsafe_allow_html=True,
        )

st.divider()

# --- ロン選択（プレイヤーがロンできる場合） ---
if "ron_available" in GS and GS.get("ron_available"):
    ron_info = GS["ron_available"]
    st.warning(
        f"🔔 {ron_info['from']} の捨て牌 [{tile_to_label(ron_info['tile'])}] でロンできます！"
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🀄 ロン！", type="primary"):
            fmt = format_result(ron_info["result"], is_tsumo=False)
            winner_hand = list(GS["hands"]["Player"]) + [ron_info["tile"]]
            GS["game_over"] = True
            GS["game_result"] = {
                "type": "ron",
                "winner": "Player",
                "loser": ron_info["from"],
                "tile": ron_info["tile"],
                "details": fmt,
                "winner_hand": winner_hand,
            }
            points = fmt["main_points"]
            GS["scores"][ron_info["from"]] -= points
            GS["scores"]["Player"] += points
            if GS["riichi_pot"] > 0:
                GS["scores"]["Player"] += GS["riichi_pot"]
                GS["riichi_pot"] = 0
            GS["ron_available"] = None
            st.rerun()
    with col2:
        if st.button("見逃す"):
            GS["ron_available"] = None
            st.rerun()

# --- ツモ和了選択（テンパイでツモったときはプレイヤーが選択） ---
if GS["turn"] == "Player" and GS.get("tsumo_available") and not GS["game_over"]:
    tsumo_info = GS["tsumo_available"]
    st.success(f"🎉 ツモれます！和了牌: {tile_to_label(tsumo_info['tile'])}")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🀄 ツモする", type="primary"):
            fmt = tsumo_info["fmt"]
            winner_hand = list(GS["hands"]["Player"])
            GS["game_over"] = True
            GS["game_result"] = {
                "type": "tsumo",
                "winner": "Player",
                "tile": tsumo_info["tile"],
                "details": fmt,
                "winner_hand": winner_hand,
            }
            apply_tsumo_points("Player", fmt)
            if GS["riichi_pot"] > 0:
                GS["scores"]["Player"] += GS["riichi_pot"]
                GS["riichi_pot"] = 0
            del GS["tsumo_available"]
            st.rerun()
    with col2:
        if st.button("ツモらない（流す）"):
            del GS["tsumo_available"]
            st.rerun()

# --- AI評価表示（直近 + Top3候補） ---
if GS.get("last_eval") and show_ai_eval:
    ev = GS["last_eval"]
    with st.expander(f"🤖 AI評価: {ev['grade']}", expanded=True):
        st.markdown(ev["message"])
        st.caption(
            f"あなたの打牌: {ev['player_tile_label']} (シャンテン: {ev['player_shanten']}) | "
            f"AI推奨: {ev['best_tile_label']} (シャンテン: {ev['best_shanten']})"
        )
        if ev.get("top_candidates"):
            st.markdown("**打牌候補 Top3**")
            for i, c in enumerate(ev["top_candidates"], 1):
                rank = "①" if i == 1 else "②" if i == 2 else "③"
                st.caption(f"{rank} {c['label']} — シャンテン{c['sh']}・有効牌約{c['acc']}種類")

# --- 手牌表示 (常時表示) ---
hand_count = len(GS["hands"]["Player"])
status_text = "（ツモ番 - 牌をクリックして打牌）" if GS["turn"] == "Player" and hand_count >= 14 else ""
st.write(f"### あなたの手牌 [{hand_count}枚] {status_text}")

# リーチ情報・リーチ宣言ボタン
if GS["riichi"]["Player"]:
    st.info("🚨 リーチ中：以後の打牌はツモ切りのみ可能です。")
elif GS.get("riichi_pending"):
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.info("🧨 リーチ宣言中：この一打の後からツモ切り固定になります。")
    with col_r2:
        if st.button("リーチをやめる"):
            GS["riichi_pending"] = False
            st.rerun()
elif (
    GS["turn"] == "Player"
    and not GS["game_over"]
    and not GS.get("tsumo_available")
    and hand_count == 14
    and GS["has_drawn"]
    and GS["scores"]["Player"] >= 1000
    and calculate_shanten_14(GS["hands"]["Player"]) == 0
):
    if st.button("🧨 リーチ宣言（1000点供託）", type="secondary"):
        GS["scores"]["Player"] -= 1000
        GS["riichi_pot"] += 1000
        GS["riichi_pending"] = True
        st.rerun()

# シャンテン数表示
if hand_count == 13:
    sh = calculate_shanten(GS["hands"]["Player"])
    if sh == 0:
        st.info("🔥 テンパイ！")
    elif sh > 0:
        st.caption(f"シャンテン数: {sh}")

player_hand_imgs = [get_tile_b64(t, "0") for t in GS["hands"]["Player"]]

with st.container():
    clicked = clickable_images(
        player_hand_imgs,
        div_style={
            "display": "flex",
            "gap": "2px",
            "justify-content": "center",
            "padding": "10px",
            "flex-wrap": "nowrap",
            "min-height": "70px",
        },
        img_style={
            "height": "60px",
            "cursor": "pointer" if GS["turn"] == "Player" and hand_count >= 14 else "default",
            "border-radius": "3px",
            "transition": "transform 0.1s",
        },
        key=f"hand_view_{GS['turn_count']}_{hand_count}",
    )

# --- プレイヤーのアクション処理 ---
if GS["turn"] == "Player" and not GS["game_over"]:
    # まだツモっていないならツモる
    if not GS["has_drawn"]:
        if not GS["wall"]:
            GS["game_over"] = True
            GS["game_result"] = {"type": "exhaustive_draw"}
            st.rerun()
        new_tile = GS["wall"].pop()
        GS["hands"]["Player"].append(new_tile)
        GS["has_drawn"] = True

        # ツモ和了: リーチ中は自動和了、それ以外はプレイヤーが選択
        winds = get_player_winds(GS["round_number"])
        tsumo_result = check_tsumo(
            GS["hands"]["Player"],
            is_riichi=GS["riichi"]["Player"],
            player_wind=winds["Player"],
            round_wind=get_round_info(GS["round_number"])["round_wind"],
            dora_indicators=[GS["dora_indicator"]],
        )
        if tsumo_result:
            fmt = format_result(tsumo_result, is_tsumo=True)
            if GS["riichi"]["Player"]:
                winner_hand = list(GS["hands"]["Player"])
                GS["game_over"] = True
                GS["game_result"] = {
                    "type": "tsumo",
                    "winner": "Player",
                    "tile": new_tile,
                    "details": fmt,
                    "winner_hand": winner_hand,
                }
                apply_tsumo_points("Player", fmt)
                if GS["riichi_pot"] > 0:
                    GS["scores"]["Player"] += GS["riichi_pot"]
                    GS["riichi_pot"] = 0
                st.rerun()
            else:
                GS["tsumo_available"] = {
                    "result": tsumo_result,
                    "fmt": fmt,
                    "tile": new_tile,
                }
                st.rerun()
        else:
            # リーチ中は自動ツモ切り（右端の牌を即座に打牌）
            if GS["riichi"]["Player"]:
                handle_player_discard(len(GS["hands"]["Player"]) - 1)
            else:
                st.rerun()

    # オートプレイ
    if auto_play and not GS["game_over"]:
        time.sleep(0.1)
        handle_player_discard(len(GS["hands"]["Player"]) - 1)

    # 手動打牌（ツモ和了選択中は打牌不可）
    if clicked > -1 and not GS["game_over"] and not GS.get("tsumo_available"):
        if GS["riichi"]["Player"] and GS["has_drawn"]:
            last_idx = len(GS["hands"]["Player"]) - 1
            if clicked != last_idx:
                st.warning("リーチ後はツモ牌のツモ切りのみ可能です。")
            else:
                handle_player_discard(clicked)
        else:
            handle_player_discard(clicked)

# --- CPU 進行 ---
elif GS["turn"] != "Player" and not GS["game_over"]:
    delay = 0.05 if auto_play else 0.4
    time.sleep(delay)
    cp = GS["turn"]

    if not GS["has_drawn"]:
        if not GS["wall"]:
            GS["game_over"] = True
            GS["game_result"] = {"type": "exhaustive_draw"}
            st.rerun()
        GS["hands"][cp].append(GS["wall"].pop())
        GS["has_drawn"] = True
        st.rerun()
    else:
        handle_cpu_turn(cp)
