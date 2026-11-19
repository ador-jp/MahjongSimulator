import streamlit as st
import random
import time
import json
import base64
from functools import lru_cache
from pathlib import Path

import streamlit.components.v1 as _components
from st_clickable_images import clickable_images
import ai_weights
import game_log
import learning
import simulation
from game_logic import _evaluate_hand_score as _eval_candidates

# 起動時にai_weights.jsonを読み込む（チューニング済みパラメータを反映）
ai_weights.load()

_COMPONENT_DIR = Path(__file__).parent / "tile_hover_component"
_tile_hover_component = _components.declare_component(
    "tile_hover_component", path=str(_COMPONENT_DIR)
)
from game_logic import (
    find_best_discard,
    check_tsumo,
    check_ron,
    is_tenpai,
    calculate_shanten,
    calculate_shanten_14,
    format_result,
    evaluate_discard,
    tile_to_label,
    can_pon,
    can_chi,
    apply_pon,
    apply_chi,
    analyze_ai_strategy,
    get_all_tile_hover_info,
)


# ==========================================
# 0. 画像パスとBase64変換
# ==========================================
@lru_cache(maxsize=512)
def get_tile_b64(tile_id, style="0"):
    is_red = tile_id.endswith("r")
    base_id = tile_id.replace("r", "")
    num, suit = base_id[0], base_id[1]
    s = {"m": "m", "p": "p", "s": "s", "z": "j"}.get(suit, suit)
    if is_red:
        red_map = {
            "3m": "c", "5m": "e", "3p": "c", "5p": "e",
            "1s": "a", "3s": "c", "5s": "e", "7s": "g",
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
    b = tile.replace("r", "")
    num_str = b[0]
    suit = b[1]
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
WIND_LABEL = {27: "東", 28: "南", 29: "西", 30: "北"}


def get_round_info(round_number: int):
    idx = max(0, round_number - 1)
    if idx >= 8:
        idx = 7
    stage = "東" if idx < 4 else "南"
    kyoku = (idx % 4) + 1
    round_wind = 27 if stage == "東" else 28
    return {
        "stage_label": stage, "kyoku": kyoku,
        "round_wind": round_wind,
        "label": f"{stage}{kyoku}局",
    }


def _current_dealer() -> str:
    return GS.get("dealer", "Player")


def get_player_winds(dealer: str):
    dealer_idx = PLAYER_ORDER.index(dealer)
    winds = {}
    wind_vals = [27, 28, 29, 30]
    for offset in range(4):
        p = PLAYER_ORDER[(dealer_idx + offset) % 4]
        winds[p] = wind_vals[offset]
    return winds


def apply_tsumo_points(winner: str, fmt: dict):
    main = fmt["main_points"]
    add = fmt["additional_points"]
    dealer = _current_dealer()
    for p in PLAYER_ORDER:
        if p == winner:
            continue
        if winner == dealer:
            GS["scores"][p] -= add
            GS["scores"][winner] += add
        else:
            if p == dealer:
                GS["scores"][p] -= main
                GS["scores"][winner] += main
            else:
                GS["scores"][p] -= add
                GS["scores"][winner] += add


def _init_round_state(scores, round_number, dealer, honba, riichi_pot):
    """ラウンド開始時の状態を構築して返す"""
    wall = [f"{i}{s}" for s in "mps" for i in range(1, 10) for _ in range(4)] + [
        f"{i}z" for i in range(1, 8) for _ in range(4)
    ]
    random.shuffle(wall)
    dora_indicator = wall[-10]

    # 親（東家）から配牌
    hands = {
        p: sorted([wall.pop() for _ in range(13)], key=tile_sort_key)
        for p in PLAYER_ORDER
    }
    # 親（東家）が14枚目をツモ → 最初のツモ番は親
    hands[dealer].append(wall.pop())

    return {
        "wall": wall,
        "hands": hands,
        "rivers": {p: [] for p in PLAYER_ORDER},
        "melds": {p: [] for p in PLAYER_ORDER},
        "turn": dealer,  # ★ 親から開始
        "has_drawn": True,  # 親は14枚持っている
        "turn_count": 0,
        "dora_indicator": dora_indicator,
        "dora_indicators": [dora_indicator],
        "game_over": False,
        "game_result": None,
        "eval_history": [],
        "last_eval": None,
        "scores": scores,
        "riichi": {p: False for p in PLAYER_ORDER},
        "riichi_pot": riichi_pot,
        "round_number": round_number,
        "riichi_pending": False,
        "dealer": dealer,
        "honba": honba,
        "decision_log": [],  # 牌譜ログ（局ごとにリセット）
    }


if "gs" not in st.session_state:
    initial_state = _init_round_state(
        scores={p: 25000 for p in PLAYER_ORDER},
        round_number=1,
        dealer="Player",
        honba=0,
        riichi_pot=0,
    )
    st.session_state.gs = initial_state

# 和了ログ（半荘をまたいで保持）
if "win_log" not in st.session_state:
    st.session_state.win_log = []

GS = st.session_state.gs

# 既存セッションに不足キーがあれば補完
for _key, _val in [
    ("round_number", 1), ("riichi", {p: False for p in PLAYER_ORDER}),
    ("riichi_pot", 0), ("riichi_pending", False),
    ("dealer", "Player"), ("honba", 0),
    ("melds", {p: [] for p in PLAYER_ORDER}),
    ("dora_indicators", [GS.get("dora_indicator", "1z")]),
    ("has_drawn", False), ("turn_count", 0),
]:
    if _key not in GS:
        GS[_key] = _val


def start_new_round(renchan: bool = False):
    scores = GS["scores"]
    honba = GS.get("honba", 0)
    riichi_pot = GS.get("riichi_pot", 0)
    current_dealer = GS.get("dealer", "Player")

    if renchan:
        dealer = current_dealer
        honba += 1
        round_number = GS["round_number"]
    else:
        honba = 0
        dealer_idx = PLAYER_ORDER.index(current_dealer)
        dealer = PLAYER_ORDER[(dealer_idx + 1) % 4]
        round_number = GS["round_number"] + 1

    new_state = _init_round_state(scores, round_number, dealer, honba, riichi_pot if renchan else 0)
    new_state["eval_history"] = GS.get("eval_history", [])
    st.session_state.gs = new_state


def start_new_game():
    """半荘終了後に新しい半荘を開始する（点数リセット）"""
    initial_scores = {p: 25000 for p in PLAYER_ORDER}
    new_state = _init_round_state(initial_scores, round_number=1, dealer="Player", honba=0, riichi_pot=0)
    new_state["eval_history"] = GS.get("eval_history", [])
    st.session_state.gs = new_state


# ==========================================
# 副露アクション
# ==========================================

def handle_player_pon(from_player, discard_tile, idx1, idx2):
    """プレイヤーのポン処理"""
    cp = "Player"
    new_hand, meld_tiles = apply_pon(GS["hands"][cp], discard_tile, idx1, idx2)
    GS["hands"][cp] = sorted(new_hand, key=tile_sort_key)
    GS["melds"][cp].append({"type": "pon", "tiles": meld_tiles})
    # ★ furo_offerを即座にクリアして無限ポン防止
    GS["furo_offer"] = None
    GS["turn"] = cp
    GS["has_drawn"] = True  # ポン後は打牌が必要


def handle_player_chi(from_player, discard_tile, idx1, idx2):
    """プレイヤーのチー処理"""
    cp = "Player"
    new_hand, meld_tiles = apply_chi(GS["hands"][cp], discard_tile, idx1, idx2)
    GS["hands"][cp] = sorted(new_hand, key=tile_sort_key)
    GS["melds"][cp].append({"type": "chi", "tiles": meld_tiles})
    # ★ furo_offerを即座にクリアして無限ポン防止
    GS["furo_offer"] = None
    GS["turn"] = cp
    GS["has_drawn"] = True


# ==========================================
# ゲームアクション
# ==========================================
_TENPAI_PAYMENT = {1: (1000, 3000), 2: (1500, 1500), 3: (3000, 1000)}
# (非テンパイ1人あたりの支払い, テンパイ1人あたりの受け取り)

def _apply_tenpai_payments():
    """流局時テンパイ払いを適用する。戻り値: {player: delta}"""
    tenpai = [p for p in PLAYER_ORDER if is_tenpai(GS["hands"][p][:13])]
    n = len(tenpai)
    if n == 0 or n == 4:
        return {}
    non_tenpai = [p for p in PLAYER_ORDER if p not in tenpai]
    pay, recv = _TENPAI_PAYMENT[n]
    for p in non_tenpai:
        GS["scores"][p] -= pay
    for p in tenpai:
        GS["scores"][p] += recv
    return {p: (recv if p in tenpai else -pay) for p in PLAYER_ORDER}


def _finalize_and_save_log(game_result: dict, scores_before: dict):
    """局終了時に牌譜を確定させてディスクに保存する"""
    log = GS.get("decision_log", [])
    if not log:
        return
    game_log.finalize_round(log, game_result, scores_before, GS["scores"])
    game_log.save_round_log(
        round_number=GS["round_number"],
        decision_log=log,
        game_result=game_result,
        scores_before=scores_before,
        scores_after=GS["scores"],
    )


def _discard_min_required():
    """副露有無に応じた打牌に必要な最低手牌枚数"""
    has_meld = bool(GS["melds"]["Player"])
    meld_count = len(GS["melds"]["Player"])
    # 副露1回=11枚+1枚ツモ=12枚から打牌, 2回=8枚+1=9枚, etc.
    if has_meld:
        return 13 - meld_count * 3 + 1  # ポン後11枚→打牌で10枚、ツモで11枚→…
    return 14


def handle_player_discard(idx):
    """プレイヤーの打牌処理 + AI評価"""
    hand = GS["hands"]["Player"]
    has_meld = bool(GS["melds"]["Player"])
    meld_count = len(GS["melds"]["Player"])
    # 副露なし: 14枚から打牌, 1副露: 12枚から打牌, 2副露: 9枚から打牌
    min_required = 14 - meld_count * 3

    if len(hand) < min_required or GS["game_over"]:
        return

    hand_before = list(GS["hands"]["Player"])

    # AI評価（リーチ後は評価しない）
    is_player_riichi = GS["riichi"]["Player"]
    if not is_player_riichi:
        context = {
            "rivers": GS["rivers"],
            "scores": GS["scores"],
            "player": "Player",
            "wall_count": len(GS["wall"]),
            "riichi": GS["riichi"],
            "dora_indicators": GS.get("dora_indicators", [GS["dora_indicator"]]),
            "melds": GS["melds"].get("Player"),
        }
        eval_result = evaluate_discard(hand_before, idx, context=context)
        GS["last_eval"] = eval_result
        GS["eval_history"].append(eval_result)
        # 牌譜ログ
        GS["decision_log"].append(game_log.make_player_decision(
            round_number=GS["round_number"],
            turn_count=GS["turn_count"],
            hand_14=hand_before,
            chosen_idx=idx,
            eval_result=eval_result,
            context=context,
        ))

    # 打牌処理
    tile = GS["hands"]["Player"].pop(idx)
    GS["rivers"]["Player"].append(tile)
    GS["hands"]["Player"].sort(key=tile_sort_key)

    # ロン判定（CPUが当たれるか）
    dealer = _current_dealer()
    winds = get_player_winds(dealer)
    for cp in ["CPU1", "CPU2", "CPU3"]:
        ron_result = check_ron(
            GS["hands"][cp], tile,
            is_riichi=GS["riichi"][cp],
            player_wind=winds[cp],
            round_wind=get_round_info(GS["round_number"])["round_wind"],
            dora_indicators=GS.get("dora_indicators", [GS["dora_indicator"]]),
            melds=GS["melds"].get(cp),
        )
        if ron_result:
            fmt = format_result(ron_result, is_tsumo=False)
            winner_hand = list(GS["hands"][cp]) + [tile]
            GS["game_over"] = True
            GS["game_result"] = {
                "type": "ron", "winner": cp, "loser": "Player",
                "tile": tile, "details": fmt, "winner_hand": winner_hand,
                "scores_before": dict(GS["scores"]),
            }
            points = fmt["main_points"]
            GS["scores"]["Player"] -= points
            GS["scores"][cp] += points
            if GS["riichi_pot"] > 0:
                GS["scores"][cp] += GS["riichi_pot"]
                GS["riichi_pot"] = 0
            _finalize_and_save_log(GS["game_result"], GS["game_result"]["scores_before"])
            st.rerun()
            return

    # リーチ確定（供託はボタン押下時に既に引いている）
    if GS.get("riichi_pending"):
        GS["riichi"]["Player"] = True
        GS["riichi_pending"] = False

    GS.pop("furo_offer", None)

    # 次のCPUへ
    current_idx = PLAYER_ORDER.index("Player")
    next_player = PLAYER_ORDER[(current_idx + 1) % 4]
    GS["turn"] = next_player
    GS["has_drawn"] = False
    GS["turn_count"] += 1
    st.rerun()


def handle_cpu_turn(cp):
    """CPU の打牌処理"""
    if GS["game_over"]:
        return

    dealer = _current_dealer()
    winds = get_player_winds(dealer)

    # ツモ判定
    tsumo_result = check_tsumo(
        GS["hands"][cp],
        is_riichi=GS["riichi"][cp],
        player_wind=winds[cp],
        round_wind=get_round_info(GS["round_number"])["round_wind"],
        dora_indicators=GS.get("dora_indicators", [GS["dora_indicator"]]),
        melds=GS["melds"].get(cp),
    )
    if tsumo_result:
        fmt = format_result(tsumo_result, is_tsumo=True)
        winner_hand = list(GS["hands"][cp])
        GS["game_over"] = True
        GS["game_result"] = {
            "type": "tsumo", "winner": cp,
            "tile": GS["hands"][cp][-1], "details": fmt,
            "winner_hand": winner_hand,
            "scores_before": dict(GS["scores"]),
        }
        apply_tsumo_points(cp, fmt)
        if GS["riichi_pot"] > 0:
            GS["scores"][cp] += GS["riichi_pot"]
            GS["riichi_pot"] = 0
        _finalize_and_save_log(GS["game_result"], GS["game_result"]["scores_before"])
        st.rerun()
        return

    context = {
        "rivers": GS["rivers"],
        "scores": GS["scores"],
        "player": cp,
        "wall_count": len(GS["wall"]),
        "riichi": GS["riichi"],
        "dora_indicators": GS.get("dora_indicators", [GS["dora_indicator"]]),
        "melds": GS["melds"].get(cp),
    }

    # 評価と実打牌で同じ候補列を共有し、重い評価の二重実行を避ける
    _cpu_candidates = _eval_candidates(GS["hands"][cp], context=context)
    if not _cpu_candidates:
        return
    best_idx = _cpu_candidates[0]["idx"]
    GS["decision_log"].append(game_log.make_cpu_decision(
        round_number=GS["round_number"],
        turn_count=GS["turn_count"],
        player=cp,
        hand_14=list(GS["hands"][cp]),
        chosen_idx=best_idx,
        candidates=_cpu_candidates,
        context=context,
    ))

    # リーチ判定（副露なし・未リーチ・1000点以上）
    if not GS["riichi"][cp] and not GS["melds"].get(cp) and GS["scores"][cp] >= 1000:
        hand_after_discard = [t for i, t in enumerate(GS["hands"][cp]) if i != best_idx]
        if calculate_shanten(hand_after_discard) == 0:
            GS["riichi"][cp] = True
            GS["scores"][cp] -= 1000
            GS["riichi_pot"] += 1000

    tile = GS["hands"][cp].pop(best_idx)
    GS["rivers"][cp].append(tile)
    GS["hands"][cp].sort(key=tile_sort_key)

    # プレイヤーのロン判定
    ron_result = check_ron(
        GS["hands"]["Player"], tile,
        is_riichi=GS["riichi"]["Player"],
        player_wind=winds["Player"],
        round_wind=get_round_info(GS["round_number"])["round_wind"],
        dora_indicators=GS.get("dora_indicators", [GS["dora_indicator"]]),
        melds=GS["melds"].get("Player"),
    )
    if ron_result:
        GS["ron_available"] = {
            "from": cp, "tile": tile, "result": ron_result,
        }
        GS["turn"] = "Player"
        GS["has_drawn"] = False
        st.rerun()
        return

    # プレイヤーの副露判定
    if not GS["riichi"]["Player"]:
        ok_pon, pon_idxs = can_pon(GS["hands"]["Player"], tile)
        chi_candidates = []
        current_idx = PLAYER_ORDER.index(cp)
        next_player = PLAYER_ORDER[(current_idx + 1) % 4]
        is_left_player = (next_player == "Player")
        if is_left_player:
            chi_candidates = can_chi(GS["hands"]["Player"], tile)

        if ok_pon or chi_candidates:
            GS["furo_offer"] = {
                "from": cp,
                "tile": tile,
                "pon_available": ok_pon,
                "pon_idxs": pon_idxs,
                "chi_candidates": chi_candidates,
            }
            GS["turn"] = "Player"
            GS["has_drawn"] = False
            st.rerun()
            return

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
st.set_page_config(
    page_title="Mahjong AI Lab",
    page_icon="🀄",
    layout="wide",
)

# --- サイドバー ---
st.sidebar.title("🀄 メニュー")
auto_play = st.sidebar.checkbox("盤面をオート進行", value=False)
show_ai_eval = st.sidebar.checkbox("🤖 AI評価を表示", value=True)
show_hover = st.sidebar.checkbox("🔍 牌ホバー情報を表示", value=False)
st.sidebar.button("🔄 リセット", on_click=lambda: st.session_state.clear())

# --- ヘッドレス高速シミュレーション ---
def _on_continuous_learning_change():
    if st.session_state.get("_continuous_learning_enabled", False):
        st.session_state.pop("_continuous_summary", None)
        st.session_state.pop("_continuous_records", None)
        st.session_state.pop("_continuous_error", None)
        st.session_state.pop("_context_evaluation", None)
    st.rerun()


@st.fragment(run_every="1s")
def _continuous_learning_controls():
    enabled = st.toggle(
        "連続学習",
        key="_continuous_learning_enabled",
        on_change=_on_continuous_learning_change,
        help="ONの間、ランダムseedで半荘を繰り返して牌譜と分析を更新します。",
    )
    summary = st.session_state.get("_continuous_summary")
    if not enabled:
        if summary:
            st.success(
                f"停止中: {summary['games']}半荘・{summary['rounds']}局を分析済み",
                icon=":material/pause_circle:",
            )
        else:
            st.caption("ONにすると、半荘数とseedの指定なしで学習を開始します。")
        return

    st.caption("稼働中。OFF操作は現在の半荘が終わった時点で反映されます。")
    if st.session_state.get("_continuous_error"):
        st.error(st.session_state["_continuous_error"])
        return

    with st.spinner("ランダムseedで次の半荘を対局・分析しています"):
        try:
            summary, records = simulation.run_continuous_step(
                summary,
                st.session_state.get("_continuous_records"),
            )
        except Exception as error:
            st.session_state["_continuous_error"] = str(error)
            st.exception(error)
            return

    st.session_state["_continuous_summary"] = summary
    st.session_state["_continuous_records"] = records
    st.session_state["_batch_summary"] = summary
    game_log.save_analysis_cache(summary["analysis"])
    st.metric(
        "連続学習",
        f"{summary['games']}半荘 / {summary['rounds']}局",
        delta=f"{summary['rounds_per_second']:.2f} 局/秒",
    )
    st.caption(
        f"最新seed: `{summary['latest_seed']}` / "
        f"直近{summary['analysis_window_rounds']}局を再分析"
    )


with st.sidebar.expander(
    "高速AI学習",
    expanded=False,
    icon=":material/bolt:",
):
    st.caption("画面描画と待機を省き、AI同士の半荘を高速に実行します。")
    _continuous_learning_controls()
    continuous_enabled = st.session_state.get(
        "_continuous_learning_enabled",
        False,
    )
    st.markdown("**固定回数で実行**")
    with st.form("batch_simulation_form"):
        batch_games = st.number_input(
            "半荘数",
            min_value=1,
            max_value=20,
            value=1,
            step=1,
            disabled=continuous_enabled,
        )
        batch_seed = st.number_input(
            "再現用seed",
            min_value=0,
            value=20260915,
            step=1,
            disabled=continuous_enabled,
        )
        persist_batch_logs = st.checkbox(
            "牌譜を保存",
            value=True,
            disabled=continuous_enabled,
        )
        run_batch = st.form_submit_button(
            "対局・分析を実行",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
            disabled=continuous_enabled,
        )

    if run_batch:
        batch_progress = st.progress(0, text="対局を準備しています")

        def _update_batch_progress(completed, total):
            batch_progress.progress(
                completed / total,
                text=f"半荘 {completed}/{total} を完了",
            )

        try:
            batch_summary = simulation.run_batch(
                games=int(batch_games),
                seed=int(batch_seed),
                persist=persist_batch_logs,
                progress=_update_batch_progress,
            )
        except Exception as error:
            st.exception(error)
        else:
            st.session_state["_batch_summary"] = batch_summary
            game_log.save_analysis_cache(batch_summary["analysis"])
            batch_progress.empty()
            st.success(
                f"{batch_summary['rounds']}局を"
                f"{batch_summary['elapsed_seconds']:.1f}秒で完了しました。"
            )

# --- 点数表示 ---
st.sidebar.markdown("---")
st.sidebar.markdown("### 💰 点数")
for p in PLAYER_ORDER:
    emoji = "🧑" if p == "Player" else "🤖"
    marker = " 👑" if p == _current_dealer() else ""
    st.sidebar.markdown(f"{emoji} **{p}**{marker}: {GS['scores'][p]:,} 点")

# --- AI評価サマリー ---
if GS["eval_history"] and show_ai_eval:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📊 AI評価サマリー")
    strictly_best = sum(1 for e in GS["eval_history"] if e.get("is_strictly_best", False))
    total = len(GS["eval_history"])
    rate = (strictly_best / total * 100) if total > 0 else 0
    avg_score = sum(e.get("total_score", 0) for e in GS["eval_history"]) / total if total > 0 else 0
    st.sidebar.markdown(f"最善手率: **{rate:.0f}%** ({strictly_best}/{total})")
    st.sidebar.markdown(f"平均スコア: **{avg_score:.1f}** /100点")

# --- AIチューニング用語解説 ---
st.sidebar.markdown("---")
with st.sidebar.expander("📖 AI用語解説", expanded=False):
    st.markdown("""
**シャンテン数 (Shanten)**
和了までに必要な交換回数。-1=和了、0=テンパイ、1=一向聴…

**受け入れ枚数 (Acceptance)**
ツモれば手が進む牌の残り枚数。数が多いほど進みやすい。

**regret（後悔値）**
実際に選んだ打牌と「最善手」のスコア差。0に近いほど最適な選択。

**ron_dealt（放銃）**
自分が切った牌で相手にロンされた回数。

**最善手率 (Optimal rate)**
AIが最もスコアの高い牌を選べた割合（%）。

**評価軸別 過小評価度**
- **シャンテン**: AIがシャンテン進行を低く見積もった度合
- **受け入れ**: 受け入れ枚数の価値を低く見積もった度合
- **ドラ**: ドラ保持価値を低く見積もった度合
- **安全**: 相手への安全度を低く見積もった度合

**テンパイ bonus**
テンパイ時にスコアに加算されるボーナス。大きいほどテンパイ維持を優先。

**リーチ bonus**
リーチ可能なテンパイ時の追加ボーナス。大きいほどリーチを積極的に宣言。

**安全 base / riichi+**
安全度スコアの重み。`base` は基本重み、`riichi+` はリーチ者がいる時の追加重み。

**acc_max / acc_norm**
受け入れ点の上限と正規化基準。`acc_norm` 枚の受け入れで `acc_max` 点になる。
""")

# --- 牌譜分析 ---
with st.sidebar.expander("📊 牌譜分析・AIチューニング", expanded=False):
    log_exists = game_log.LOG_FILE.exists()
    if log_exists:
        rounds = game_log.load_logs(max_rounds=200)
        st.caption(f"記録済み局数: **{len(rounds)}**")
    else:
        rounds = []
        st.caption("まだ牌譜がありません。オートプレイで対局してください。")

    col_a, col_b = st.columns(2)
    with col_a:
        run_analysis = st.button("🔍 分析実行", width="stretch")
    with col_b:
        if st.button("🗑 ログ削除", width="stretch"):
            game_log.clear_logs()
            st.rerun()

    if run_analysis and rounds:
        analysis = game_log.analyze(rounds)
        game_log.save_analysis_cache(analysis)
        st.session_state["_analysis_result"] = analysis

    analysis = st.session_state.get("_analysis_result") or game_log.load_analysis_cache()
    if analysis:
        n = analysis.get("total_rounds", 0)
        st.markdown(f"**集計局数: {n}**")
        st.markdown(
            f"勝率 **{analysis['win_rate']:.0f}%** / "
            f"流局 **{analysis['draw_rate']:.0f}%** / "
            f"失点 **{analysis['lose_rate']:.0f}%**"
        )
        st.markdown(
            f"最善手率 **{analysis['optimal_rate']:.0f}%** | "
            f"平均regret **{analysis['avg_regret']:.1f}**"
        )
        st.markdown(
            f"放銃率 **{analysis['ron_dealt_rate']:.0f}%** "
            f"（回避可能: {analysis['ron_avoidable']}件）"
        )
        cr = analysis.get("comp_regret", {})
        if cr:
            st.caption("評価軸別の過小評価度（正＝その軸を低く見積もっている）")
            st.caption(
                f"シャンテン:{cr.get('sh',0):+.1f} "
                f"受け入れ:{cr.get('acc',0):+.1f} "
                f"ドラ:{cr.get('dora',0):+.1f} "
                f"安全:{cr.get('safety',0):+.1f}"
            )

        # 集計だけの案は参考表示に留め、局面別A/B検証を通過するまで適用しない
        _, notes = game_log.suggest_tuning(analysis)
        st.markdown("**参考診断:**")
        for note in notes:
            st.caption(f"• {note}")
        st.caption("重みの反映は高速学習レポートのA/B検証後に行います。")

    # 現在の重みを表示
    with st.expander("現在のAI重み", expanded=False):
        w = ai_weights.get()
        st.caption(f"安全: base={w['safety_base']:.3f} / riichi+={w['safety_per_riichi']:.3f}")
        st.caption(f"受け入れ: max={w['acc_max']:.1f} / norm={w['acc_norm']:.1f}")
        st.caption(f"ドラ: {w['dora_per_tile']:.1f}pt/枚 max={w['dora_max']:.1f}")
        st.caption(f"テンパイ bonus={w['tenpai_bonus']:.1f} / リーチ+={w['riichi_eligible_bonus']:.1f}")
        if st.button("🔄 デフォルトにリセット", width="stretch"):
            ai_weights.reset_to_default()
            st.success("デフォルト重みに戻しました。")
            st.rerun()

# --- 高速学習ダッシュボード ---
batch_summary = st.session_state.get("_batch_summary")
if batch_summary:
    batch_analysis = batch_summary["analysis"]
    with st.container(border=True):
        st.subheader("高速AI学習レポート", icon=":material/analytics:")
        if batch_summary.get("continuous"):
            st.caption(
                f"連続Run {batch_summary['run_id']} / "
                f"最新seed {batch_summary['latest_seed']} / "
                f"累計{batch_summary['elapsed_seconds']:.1f}秒 / "
                f"直近{batch_summary['analysis_window_rounds']}局を分析"
            )
        else:
            st.caption(
                f"Run {batch_summary['run_id']} / seed {batch_summary['seed']} / "
                f"{batch_summary['elapsed_seconds']:.1f}秒"
            )
        with st.container(horizontal=True):
            st.metric("半荘", batch_summary["games"], border=True)
            st.metric("局数", batch_summary["rounds"], border=True)
            st.metric(
                "処理速度",
                f"{batch_summary['rounds_per_second']:.2f} 局/秒",
                border=True,
            )
            st.metric(
                "終盤聴牌率",
                f"{batch_analysis.get('late_tenpai_rate', 0):.1f}%",
                border=True,
            )
            st.metric(
                "放銃局率",
                f"{batch_analysis.get('ron_dealt_rate', 0):.1f}%",
                border=True,
            )

        result_column, placement_column = st.columns(2)
        with result_column:
            st.markdown("**局結果**")
            result_labels = {
                "tsumo": "ツモ",
                "ron": "ロン",
                "exhaustive_draw": "流局",
                "invalid": "無効",
            }
            result_rows = [
                {"結果": result_labels.get(result, result), "局数": count}
                for result, count in batch_summary["result_counts"].items()
            ]
            if result_rows:
                st.bar_chart(result_rows, x="結果", y="局数")
        with placement_column:
            st.markdown("**Player順位**")
            placement_rows = [
                {"順位": f"{place}位", "半荘数": count}
                for place, count in batch_summary["player_placements"].items()
            ]
            st.bar_chart(placement_rows, x="順位", y="半荘数")

        st.markdown("**良かった点・改善点**")
        for diagnostic in batch_analysis.get("diagnostics", []):
            status = diagnostic.get("status")
            message = f"**{diagnostic['title']}** — {diagnostic['detail']}"
            if status == "good":
                st.success(message, icon=":material/check_circle:")
            elif status == "bad":
                st.warning(message, icon=":material/warning:")
            else:
                st.info(message, icon=":material/info:")

        st.markdown("**具体的な局面分析**")
        batch_records = (
            game_log.load_logs_for_run(
                batch_summary["run_id"],
                max_rounds=batch_summary.get("analysis_window_rounds", 500),
            )
            if batch_summary.get("persisted")
            else []
        )
        if not batch_records:
            st.info(
                "局面別学習には、対局時に「牌譜を保存」を有効にしてください。",
                icon=":material/database:",
            )
        else:
            decision_examples = learning.extract_decision_examples(batch_records)
            with st.expander(
                "具体的な好手・悪手を見る",
                icon=":material/find_in_page:",
            ):
                bad_rows = [
                    {
                        "状況": example["context_label"],
                        "手牌": " ".join(example["hand"]),
                        "選択": example["chosen"].get("tile"),
                        "代替": (example.get("alternative") or {}).get("tile", "—"),
                        "判断": example["reason"],
                    }
                    for example in decision_examples["bad"]
                ]
                good_rows = [
                    {
                        "状況": example["context_label"],
                        "手牌": " ".join(example["hand"]),
                        "選択": example["chosen"].get("tile"),
                        "判断": example["reason"],
                    }
                    for example in decision_examples["good"]
                ]
                st.markdown("**改善すべき判断**")
                if bad_rows:
                    st.dataframe(bad_rows, hide_index=True)
                else:
                    st.caption("このRunでは説明可能な重大悪手を検出しませんでした。")
                st.markdown("**良かった判断**")
                if good_rows:
                    st.dataframe(good_rows, hide_index=True)
                else:
                    st.caption("好手判定に十分な局面がありませんでした。")

            context_proposal = learning.propose_context_weights(batch_records)
            st.markdown("**局面別の重み候補**")
            if not context_proposal["changes"]:
                st.info(
                    "局面別変更の根拠が不足しています。牌譜を追加してください。",
                    icon=":material/info:",
                )
            else:
                st.dataframe(
                    [
                        {
                            "対象局面": change["label"],
                            "判断数": change["sample_size"],
                            "変更": json.dumps(change["profile"], ensure_ascii=False),
                            "根拠": " / ".join(change["reasons"]),
                        }
                        for change in context_proposal["changes"]
                    ],
                    hide_index=True,
                )
                st.caption(
                    "採用条件: 学習seed・未使用seedの両方で、平均点差と平均順位が改善し、"
                    "放銃局率と終盤聴牌率が悪化しないこと。"
                )
                evaluation_rounds = st.number_input(
                    "A/B検証の局数（学習用・未使用seed用それぞれ）",
                    min_value=1,
                    max_value=30,
                    value=4,
                    step=1,
                    key=f"evaluation_rounds_{batch_summary['run_id']}",
                )
                max_ui_seed = (1 << 53) - 1
                evaluation_seed = st.number_input(
                    "A/B検証seed",
                    min_value=0,
                    max_value=max_ui_seed,
                    value=min(int(batch_summary["seed"]), max_ui_seed),
                    step=1,
                    key=f"evaluation_seed_safe_{batch_summary['run_id']}",
                )
                if st.button(
                    "同一seed A/Bと未使用seed検証を実行",
                    type="primary",
                    icon=":material/science:",
                    key=f"evaluate_context_{batch_summary['run_id']}",
                ):
                    evaluation_status = st.status(
                        "局面別重みを検証しています",
                        expanded=True,
                    )
                    stage_labels = {
                        "training_baseline": "学習seed: 現行AI",
                        "training_candidate": "学習seed: 候補AI",
                        "validation_baseline": "未使用seed: 現行AI",
                        "validation_candidate": "未使用seed: 候補AI",
                    }

                    def _show_evaluation_stage(stage, completed, total):
                        evaluation_status.write(
                            f"{completed}/{total} {stage_labels[stage]}"
                        )

                    context_evaluation = learning.evaluate_context_candidate(
                        context_proposal,
                        seed=int(evaluation_seed),
                        training_rounds=int(evaluation_rounds),
                        validation_rounds=int(evaluation_rounds),
                        progress=_show_evaluation_stage,
                    )
                    st.session_state["_context_evaluation"] = {
                        "run_id": batch_summary["run_id"],
                        "result": context_evaluation,
                    }
                    evaluation_status.update(
                        label="A/B検証が完了しました",
                        state="complete",
                    )

                stored_evaluation = st.session_state.get("_context_evaluation", {})
                if stored_evaluation.get("run_id") == batch_summary["run_id"]:
                    context_evaluation = stored_evaluation["result"]
                    comparison_rows = []
                    for label, comparison in (
                        ("学習seed", context_evaluation["training"]),
                        ("未使用seed", context_evaluation["validation"]),
                    ):
                        comparison_rows.append({
                            "データ": label,
                            "平均点差の改善": comparison["score_gain"],
                            "平均順位の改善": comparison["placement_gain"],
                            "勝率差": comparison["win_rate_gain"],
                            "放銃局率差": comparison["deal_in_rate_change"],
                            "終盤聴牌率差": comparison["late_tenpai_rate_change"],
                        })
                    st.dataframe(comparison_rows, hide_index=True)

                    if context_evaluation["accepted"]:
                        st.success(
                            "学習seedと未使用seedの両方で採用基準を通過しました。",
                            icon=":material/verified:",
                        )
                        applied_runs = st.session_state.get(
                            "_applied_context_runs", []
                        )
                        if batch_summary["run_id"] in applied_runs:
                            st.success("この局面学習は反映済みです。")
                        elif st.button(
                            "検証済みの局面学習をAIへ反映",
                            type="primary",
                            icon=":material/neurology:",
                            key=f"apply_context_{batch_summary['run_id']}",
                        ):
                            learning.apply_validated_candidate(context_evaluation)
                            st.session_state["_applied_context_runs"] = [
                                *applied_runs,
                                batch_summary["run_id"],
                            ]
                            st.toast("検証済みの局面重みを保存しました。")
                            st.rerun()
                    else:
                        st.warning(
                            "採用基準を満たさなかったため、AI重みは変更しません。",
                            icon=":material/block:",
                        )

        st.download_button(
            "分析結果をJSONで保存",
            data=json.dumps(batch_summary, ensure_ascii=False, indent=2),
            file_name=f"mahjong-ai-{batch_summary['run_id']}.json",
            mime="application/json",
            icon=":material/download:",
        )

        tuning_history = game_log.load_tuning_history(max_entries=5)
        if tuning_history:
            with st.expander("直近のAI調整履歴", icon=":material/history:"):
                st.dataframe(
                    [
                        {
                            "適用元": record.get("source", ""),
                            "変更": json.dumps(record.get("diff", {}), ensure_ascii=False),
                            "対象局数": record.get("metrics", {}).get("total_rounds"),
                        }
                        for record in reversed(tuning_history)
                    ],
                    hide_index=True,
                )

# --- 場・局・風表示 ---
round_info = get_round_info(GS["round_number"])
dealer = _current_dealer()
winds = get_player_winds(dealer)
st.sidebar.markdown("---")
honba_str = f" {GS.get('honba', 0)}本場" if GS.get("honba", 0) > 0 else ""
st.sidebar.markdown(f"**{round_info['label']}{honba_str}（場風: {round_info['stage_label']}）**")
st.sidebar.markdown("**自風**")
for p in PLAYER_ORDER:
    seat = WIND_LABEL[winds[p]]
    emoji = "🧑" if p == "Player" else "🤖"
    st.sidebar.caption(f"{emoji} {p}: {seat}家")

# --- ゲームオーバー / 流局 表示 ---
if GS["game_over"] and GS["game_result"]:
    result = GS["game_result"]
    is_draw = result.get("type") == "exhaustive_draw"

    if is_draw:
        st.warning("🏁 **流局** - 山札がなくなりました。")
    else:
        details = result["details"]
        winner = result["winner"]
        if result["type"] == "tsumo":
            st.success(f"🎉 **ツモ！** {winner} がアガりました！")
        else:
            st.error(f"🔥 **ロン！** {winner} が {result['loser']} からアガりました！")

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

        if "winner_hand" in result and result["winner_hand"]:
            st.markdown("---")
            st.markdown(f"**{winner} の上がり手（最終手牌）**")
            win_hand = sorted(result["winner_hand"], key=tile_sort_key)
            hand_imgs = "".join(
                f'<img src="{get_tile_b64(t, "0")}" style="height:44px; margin:1px;">' for t in win_hand
            )
            st.markdown(
                f'<div style="display:flex; flex-wrap:wrap;">{hand_imgs}</div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")
    scores_before = result.get("scores_before", {})
    if scores_before:
        st.markdown("### 点数変動")
        delta_cols = st.columns(4)
        player_labels = {"Player": "あなた", "CPU1": "CPU1", "CPU2": "CPU2", "CPU3": "CPU3"}
        for i, p in enumerate(PLAYER_ORDER):
            with delta_cols[i]:
                before = scores_before.get(p, GS["scores"][p])
                after = GS["scores"][p]
                delta = after - before
                name = player_labels.get(p, p)
                delta_str = f"+{delta:,}" if delta > 0 else f"{delta:,}" if delta < 0 else "±0"
                color = "#4caf50" if delta > 0 else "#f44336" if delta < 0 else "#888"
                st.markdown(f"**{name}**")
                st.markdown(
                    f"<span style='color:{color};font-weight:bold;font-size:1.1em;'>{delta_str}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"{after:,}点")
    is_renchan = (not is_draw) and (result.get("winner") == _current_dealer())
    is_hanchan_over = GS["round_number"] >= 8 and not is_renchan

    # オートプレイ時は自動進行
    if auto_play:
        time.sleep(0.3)
        if is_hanchan_over:
            start_new_game()
        elif is_renchan:
            start_new_round(renchan=True)
        else:
            start_new_round(renchan=False)
        st.rerun()

    if is_hanchan_over:
        st.markdown("🏁 半荘終了（リセットで新しい半荘を開始できます）")
    else:
        if is_renchan:
            st.info(f"👑 **連荘！** {_current_dealer()} が親継続です。（{GS.get('honba', 0) + 1}本場）")
            if st.button("▶ 連荘（次の局へ）", type="primary"):
                start_new_round(renchan=True)
                st.rerun()
        else:
            if st.button("▶ 次の局へ", type="primary"):
                start_new_round(renchan=False)
                st.rerun()
    st.stop()

# --- 対局ボード ---
round_info = get_round_info(GS["round_number"])
honba_str = f" {GS.get('honba', 0)}本場" if GS.get("honba", 0) > 0 else ""

# --- 河（麻雀卓レイアウト）---
def _make_meld_html(melds_list):
    """副露牌のHTMLを生成"""
    if not melds_list:
        return ""
    html = ""
    for m in melds_list:
        mtype = m["type"]
        label = "ポン" if mtype == "pon" else "チー" if mtype == "chi" else "カン"
        tiles_html = ""
        for j, t in enumerate(m["tiles"]):
            tile_style = "2" if j == 0 else "1"
            b64 = get_tile_b64(t, tile_style)
            if b64:
                h = "26px" if j == 0 else "34px"
                tiles_html += f'<img src="{b64}" style="height:{h}; margin:1px; vertical-align:middle;">'
        html += (
            f'<div style="display:inline-flex;align-items:center;gap:1px;'
            f'background:rgba(255,255,255,0.1);padding:2px 4px;border-radius:4px;margin:1px;">'
            f'<span style="font-size:0.6em;color:#ccc;margin-right:1px;">{label}</span>'
            f'{tiles_html}</div>'
        )
    return html


def _player_label(p):
    riichi_icon = "🚨" if GS["riichi"][p] else ""
    dealer_icon = "👑" if p == _current_dealer() else ""
    turn_icon = " ▶" if p == GS["turn"] else ""
    wind = WIND_LABEL[winds[p]]
    name = "あなた" if p == "Player" else p
    return f"{name} {dealer_icon}{riichi_icon}{turn_icon} ({wind}家)"


def _river_tiles_html_rows(p, rotate_deg=0, per_row=6, reverse_rows=False):
    """河の牌HTMLを生成。1行per_row枚で折り返し。reverse_rows=Trueで最新行を先頭に"""
    tiles = GS["rivers"][p]
    if not tiles:
        return ""
    rot = f"transform:rotate({rotate_deg}deg);" if rotate_deg else ""
    rows = []
    for row_start in range(0, len(tiles), per_row):
        row = tiles[row_start:row_start + per_row]
        row_html = "".join(
            f'<img src="{get_tile_b64(t, "1")}" style="height:42px;margin:1px;{rot}">'
            for t in row
        )
        rows.append(f'<div style="display:flex;">{row_html}</div>')
    if reverse_rows:
        rows.reverse()
    return "".join(rows)


# セルスタイルの共通設定
_cell_base = "background:rgba(0,0,0,0.35);border-radius:6px;padding:4px;"

# 横河（上・下）のHTMLブロック生成（1行6枚折り返し）
def _horiz_river_block(p, rotate=False):
    lbl = _player_label(p)
    # CPU2（上）: 新しい行が手前（上）→ reverse_rows=True
    river_html = _river_tiles_html_rows(p, rotate_deg=180 if rotate else 0, per_row=6, reverse_rows=rotate)
    meld_html = _make_meld_html(GS["melds"].get(p, []))
    label_style = "text-align:left;color:#eee;font-size:0.8em;margin-bottom:2px;"
    if rotate:
        # CPU2（上）はラベルを下に
        return (
            f'<div style="{_cell_base}min-height:80px;">'
            f'<div style="display:flex;flex-direction:column;align-items:flex-start;min-height:60px;">{river_html}</div>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:flex-start;">{meld_html}</div>'
            f'<div style="{label_style}">{lbl}</div>'
            f'</div>'
        )
    else:
        return (
            f'<div style="{_cell_base}min-height:80px;">'
            f'<div style="{label_style}">{lbl}</div>'
            f'<div style="display:flex;flex-direction:column;align-items:flex-start;min-height:60px;">{river_html}</div>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:flex-start;">{meld_html}</div>'
            f'</div>'
        )


# 縦河（左・右）HTMLブロック生成（横向き牌style "2"を1列6枚で縦並べ）
def _vert_river_block(p, rotate_deg=0, reverse_cols=False):
    """
    reverse_cols=True: 新しい列を左側（手前）に配置（CPU3用）
    reverse_cols=False: 新しい列を右側（手前）に配置（CPU1用）
    """
    lbl = _player_label(p)
    tiles = GS["rivers"][p]
    rot = f"transform:rotate({rotate_deg}deg);" if rotate_deg else ""
    # 6枚ずつ列に分ける
    columns = []
    for col_start in range(0, max(len(tiles), 1), 6):
        col_tiles = tiles[col_start:col_start + 6]
        col_imgs = "".join(
            f'<img src="{get_tile_b64(t, "2")}" style="height:38px;margin:2px;display:block;{rot}">'
            for t in col_tiles
        )
        columns.append(f'<div style="display:flex;flex-direction:column;align-items:center;">{col_imgs}</div>')
    if reverse_cols:
        columns.reverse()
    cols_html = "".join(columns)
    meld_html = _make_meld_html(GS["melds"].get(p, []))
    label_style = "color:#eee;font-size:0.75em;writing-mode:vertical-rl;text-orientation:mixed;margin:4px 0;"
    return (
        f'<div style="{_cell_base}display:flex;flex-direction:column;align-items:flex-start;justify-content:center;min-width:80px;">'
        f'<div style="{label_style}">{lbl}</div>'
        f'<div style="display:flex;flex-direction:row;">{cols_html}</div>'
        f'<div>{meld_html}</div>'
        f'</div>'
    )


# 中央セル（局情報・ドラ）
dora_center = ""
for d in GS.get("dora_indicators", [GS["dora_indicator"]]):
    b64 = get_tile_b64(d, "1")
    if b64:
        dora_center += f'<img src="{b64}" style="height:32px;margin:1px;">'

center_html = (
    f'<div style="{_cell_base}display:flex;flex-direction:column;align-items:center;'
    f'justify-content:center;min-height:120px;gap:4px;">'
    f'<div style="color:#ffd700;font-weight:bold;font-size:0.9em;">{round_info["label"]}{honba_str}</div>'
    f'<div style="color:#aaa;font-size:0.75em;">残り {len(GS["wall"])} 枚</div>'
    f'<div style="color:#aaa;font-size:0.7em;margin-top:2px;">ドラ表示</div>'
    f'<div style="display:flex;flex-wrap:wrap;justify-content:center;">{dora_center}</div>'
    f'</div>'
)

table_html = f"""
<div style="
  display:grid;
  grid-template-columns:clamp(70px,10%,110px) 1fr clamp(70px,10%,110px);
  grid-template-rows:auto auto auto;
  gap:4px;
  background:#1a5c35;
  border-radius:12px;
  padding:6px;
  margin-bottom:4px;
">
  <div style="grid-column:1/4;">{_horiz_river_block("CPU2", rotate=True)}</div>
  {_vert_river_block("CPU3", rotate_deg=180, reverse_cols=True)}
  {center_html}
  {_vert_river_block("CPU1", rotate_deg=0, reverse_cols=False)}
  <div style="grid-column:1/4;">{_horiz_river_block("Player", rotate=False)}</div>
</div>
"""
st.markdown(table_html, unsafe_allow_html=True)

# ==========================================
# 手牌表示関数（再利用可能）
# ==========================================
def _render_hand():
    """手牌を常に表示する（副露オファー中や判定中でも表示）"""
    hand_count = len(GS["hands"]["Player"])
    has_meld = bool(GS["melds"]["Player"])
    meld_count = len(GS["melds"]["Player"])
    discard_ready = GS["turn"] == "Player" and GS["has_drawn"] and (
        (not has_meld and hand_count >= 14) or
        (has_meld and hand_count >= (14 - meld_count * 3))
    )
    status_text = "(ツモ番 - 牌をクリックして打牌)" if discard_ready else ""
    st.write(f"### あなたの手牌 [{hand_count}枚] {status_text}")

    # リーチ表示
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
        and not GS["melds"]["Player"]
    ):
        if st.button("🧨 リーチ宣言（1000点供託）", type="secondary"):
            GS["scores"]["Player"] -= 1000
            GS["riichi_pot"] += 1000
            GS["riichi_pending"] = True
            st.rerun()

    # シャンテン数表示
    if hand_count <= 13:
        sh = calculate_shanten(GS["hands"]["Player"])
        if sh == 0:
            st.info("🔥 テンパイ！")
        elif sh > 0:
            st.caption(f"シャンテン数: {sh}")

    # ホバーtoolip付き手牌表示（カスタムコンポーネントで hover + click を統合）
    if show_hover:
        context = {
            "rivers": GS["rivers"], "scores": GS["scores"],
            "player": "Player", "wall_count": len(GS["wall"]),
            "riichi": GS["riichi"],
            "dora_indicators": GS.get("dora_indicators", [GS["dora_indicator"]]),
            "melds": GS["melds"].get("Player"),
        }
        # ホバー情報をセッションキャッシュ（手牌・リーチ状況・総捨て枚数が変わった時のみ再計算）
        _hcache_key = (
            tuple(GS["hands"]["Player"]),
            tuple(sorted(GS["riichi"].items())),
            sum(len(r) for r in GS["rivers"].values()),
        )
        if st.session_state.get("_hover_cache_key") == _hcache_key:
            hover_infos = st.session_state["_hover_cache_val"]
        else:
            hover_infos = get_all_tile_hover_info(GS["hands"]["Player"], context=context)
            st.session_state["_hover_cache_key"] = _hcache_key
            st.session_state["_hover_cache_val"] = hover_infos
        tiles_data = []
        for i, tile in enumerate(GS["hands"]["Player"]):
            b64 = get_tile_b64(tile, "0")
            info = hover_infos[i] if i < len(hover_infos) else ""
            info_html = info.replace("\n", "<br>")
            while "**" in info_html:
                info_html = info_html.replace("**", "<b>", 1)
                info_html = info_html.replace("**", "</b>", 1)
            tiles_data.append({"src": b64, "info": info_html})
        if discard_ready:
            st.caption("牌にホバーで評価確認 / クリックして打牌")
        clicked = _tile_hover_component(
            tiles=tiles_data,
            clickable=discard_ready,
            key=f"hand_hover_{GS['turn_count']}_{hand_count}_{len(GS['melds']['Player'])}",
            default=-1,
        )
        return (clicked if clicked is not None else -1), discard_ready

    # 通常手牌表示 (clickable_images)
    player_hand_imgs = [get_tile_b64(t, "0") for t in GS["hands"]["Player"]]
    clicked = clickable_images(
        player_hand_imgs,
        div_style={
            "display": "flex", "gap": "2px", "justify-content": "center",
            "padding": "10px", "flex-wrap": "nowrap", "min-height": "70px",
        },
        img_style={
            "height": "60px",
            "cursor": "pointer" if discard_ready else "default",
            "border-radius": "3px",
            "transition": "transform 0.1s",
        },
        key=f"hand_view_{GS['turn_count']}_{hand_count}_{len(GS['melds']['Player'])}",
    )
    return clicked, discard_ready


# ==========================================
# 手牌を卓の直下に表示（常にここで一度だけ描画）
# ==========================================
clicked, discard_ready = _render_hand()

# ==========================================
# ロン・副露オファー・ツモ判断
# ==========================================

# --- ロン選択 ---
if GS.get("ron_available") and not GS["game_over"]:
    ron_info = GS["ron_available"]

    def _do_ron():
        fmt = format_result(ron_info["result"], is_tsumo=False)
        winner_hand = list(GS["hands"]["Player"]) + [ron_info["tile"]]
        GS["game_over"] = True
        GS["game_result"] = {
            "type": "ron", "winner": "Player", "loser": ron_info["from"],
            "tile": ron_info["tile"], "details": fmt, "winner_hand": winner_hand,
            "scores_before": dict(GS["scores"]),
        }
        points = fmt["main_points"]
        GS["scores"][ron_info["from"]] -= points
        GS["scores"]["Player"] += points
        if GS["riichi_pot"] > 0:
            GS["scores"]["Player"] += GS["riichi_pot"]
            GS["riichi_pot"] = 0
        GS["ron_available"] = None
        _finalize_and_save_log(GS["game_result"], GS["game_result"]["scores_before"])

    if auto_play:
        _do_ron()
        st.rerun()
    else:
        st.warning(
            f"🔔 {ron_info['from']} の捨て牌 **[{tile_to_label(ron_info['tile'])}]** でロンできます！"
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🀄 ロン！", type="primary"):
                _do_ron()
                st.rerun()
        with col2:
            if st.button("見逃す"):
                GS["ron_available"] = None
                current_idx = PLAYER_ORDER.index(ron_info["from"])
                next_player = PLAYER_ORDER[(current_idx + 1) % 4]
                GS["turn"] = next_player
                GS["has_drawn"] = False
                st.rerun()
    st.stop()

# --- 副露オファー ---
if GS.get("furo_offer") and not GS["game_over"] and not GS.get("ron_available"):
    offer = GS["furo_offer"]
    tile = offer["tile"]
    from_p = offer["from"]

    def _do_furo_skip():
        GS["furo_offer"] = None
        current_idx = PLAYER_ORDER.index(from_p)
        next_player = PLAYER_ORDER[(current_idx + 1) % 4]
        GS["turn"] = next_player
        GS["has_drawn"] = False

    if auto_play:
        _do_furo_skip()
        st.rerun()
    else:
        st.info(f"📢 **[{tile_to_label(tile)}]** （{from_p} の捨て牌）に対して副露できます！")

        # 現在の手牌でポン・チーを再検証（stale indexによる無限副露バグ防止）
        ok_pon_now, pon_idxs_now = can_pon(GS["hands"]["Player"], tile)
        chi_candidates_now = offer.get("chi_candidates", [])

        n_buttons = 1  # スルーボタン
        if offer.get("pon_available") and ok_pon_now:
            n_buttons += 1
        n_buttons += len(chi_candidates_now)
        furo_cols = st.columns(max(n_buttons, 1))
        col_idx = 0

        if offer.get("pon_available") and ok_pon_now:
            with furo_cols[col_idx]:
                if st.button("🀄 ポン", type="primary"):
                    idx1, idx2 = pon_idxs_now
                    handle_player_pon(from_p, tile, idx1, idx2)
                    st.rerun()
            col_idx += 1

        for j, (ci1, ci2) in enumerate(chi_candidates_now):
            player_hand = GS["hands"]["Player"]
            if ci1 >= len(player_hand) or ci2 >= len(player_hand):
                continue
            t1 = player_hand[ci1]
            t2 = player_hand[ci2]
            chi_label = f"チー [{tile_to_label(t1)}+{tile_to_label(t2)}]"
            with furo_cols[col_idx]:
                if st.button(chi_label, key=f"chi_{j}"):
                    handle_player_chi(from_p, tile, ci1, ci2)
                    st.rerun()
            col_idx += 1

        with furo_cols[col_idx]:
            if st.button("スルー"):
                _do_furo_skip()
                st.rerun()
    st.stop()

# --- ツモ和了選択 ---
if GS["turn"] == "Player" and GS.get("tsumo_available") and not GS["game_over"]:
    tsumo_info = GS["tsumo_available"]

    def _do_tsumo():
        fmt = tsumo_info["fmt"]
        winner_hand = list(GS["hands"]["Player"])
        GS["game_over"] = True
        GS["game_result"] = {
            "type": "tsumo", "winner": "Player",
            "tile": tsumo_info["tile"], "details": fmt,
            "winner_hand": winner_hand,
            "scores_before": dict(GS["scores"]),
        }
        apply_tsumo_points("Player", fmt)
        if GS["riichi_pot"] > 0:
            GS["scores"]["Player"] += GS["riichi_pot"]
            GS["riichi_pot"] = 0
        del GS["tsumo_available"]
        _finalize_and_save_log(GS["game_result"], GS["game_result"]["scores_before"])

    if auto_play:
        _do_tsumo()
        st.rerun()
    else:
        st.success(f"🎉 ツモれます！和了牌: {tile_to_label(tsumo_info['tile'])}")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🀄 ツモする", type="primary"):
                _do_tsumo()
                st.rerun()
    with col2:
        if st.button("ツモらない（流す）"):
            del GS["tsumo_available"]
            st.rerun()

# --- AI評価表示 ---
if GS.get("last_eval") and show_ai_eval:
    ev = GS["last_eval"]
    score_str = f"{ev.get('total_score', '?'):.0f}/100点" if isinstance(ev.get('total_score'), (int, float)) else ""
    show_detail = st.checkbox(
        f"🤖 AI評価: {ev['grade']} {score_str}",
        value=True,
        key="ai_eval_detail_toggle",
    )
    if show_detail:
        st.markdown(ev["message"])
        st.caption(
            f"あなたの打牌: {ev['player_tile_label']} (シャンテン: {ev['player_shanten']}) | "
            f"AI推奨: {ev['best_tile_label']} (シャンテン: {ev['best_shanten']})"
        )
        if ev.get("top_candidates"):
            st.markdown("**打牌候補 Top3**")
            for i, c in enumerate(ev["top_candidates"], 1):
                rank = "①" if i == 1 else "②" if i == 2 else "③"
                st.caption(
                    f"{rank} **{c['label']}** — "
                    f"**{c['total_score']:.0f}/100点** "
                    f"— {c.get('danger_description', '')}"
                )

        # ★ AI思考表示
        if ev.get("ai_thinking"):
            at = ev["ai_thinking"]
            strategy_icon = {"攻め": "⚔️", "攻め（テンパイ）": "🎯", "攻め（イーシャンテン）": "⚔️",
                             "降り": "🛡️", "様子見": "👀"}.get(at["strategy"], "🤔")
            st.markdown("---")
            st.markdown(f"### {strategy_icon} AIの思考: **{at['strategy']}**")
            st.markdown(f"**狙い:** {at['target']}")
            st.markdown(f"**理由:** {at['reasoning']}")

# --- CPU 思考状況表示（プレイヤーターン時のみ計算して高速化）---
if show_ai_eval:
    with st.expander("🤖 CPU の思考状況（予測）", expanded=False):
        if GS["turn"] != "Player":
            st.caption("CPU 思考中...")
        else:
            cpu_strategy_cols = st.columns(3)
            strategy_icon_map = {
                "攻め": "⚔️", "攻め（テンパイ）": "🎯", "攻め（イーシャンテン）": "⚔️",
                "降り": "🛡️", "様子見": "👀", "不明": "❓",
            }
            for ci, cp in enumerate(["CPU1", "CPU2", "CPU3"]):
                with cpu_strategy_cols[ci]:
                    cp_context = {
                        "rivers": GS["rivers"],
                        "scores": GS["scores"],
                        "player": cp,
                        "wall_count": len(GS["wall"]),
                        "riichi": GS["riichi"],
                    }
                    cp_hand = GS["hands"][cp]
                    cp_strategy = analyze_ai_strategy(cp_hand, context=cp_context)
                    s_icon = strategy_icon_map.get(cp_strategy["strategy"], "🤔")
                    riichi_icon = "🚨" if GS["riichi"][cp] else ""
                    dealer_icon = "👑" if cp == _current_dealer() else ""
                    sh_val = cp_strategy.get("shanten", 99)
                    sh_label = {-1: "和了形", 0: "テンパイ", 1: "イーシャンテン"}.get(sh_val, f"{sh_val}シャンテン")
                    st.markdown(f"**{cp}** {dealer_icon}{riichi_icon}")
                    st.markdown(f"{s_icon} **{cp_strategy['strategy']}**")
                    st.caption(f"状態: {sh_label}")
                    target_short = cp_strategy["target"].replace("**", "").replace("混一色", "混一").replace("七対子", "七対")
                    st.caption(f"狙い: {target_short[:60]}")
                    if cp_strategy.get("reasoning"):
                        st.caption(cp_strategy["reasoning"][:80])

# --- プレイヤーのアクション処理 ---
if GS["turn"] == "Player" and not GS["game_over"] and not GS.get("ron_available") and not GS.get("furo_offer"):
    # まだツモっていないならツモる
    if not GS["has_drawn"]:
        if not GS["wall"]:
            scores_before_draw = dict(GS["scores"])
            _apply_tenpai_payments()
            GS["game_over"] = True
            GS["game_result"] = {"type": "exhaustive_draw", "scores_before": scores_before_draw}
            _finalize_and_save_log(GS["game_result"], scores_before_draw)
            st.rerun()
        new_tile = GS["wall"].pop()
        GS["hands"]["Player"].append(new_tile)
        GS["has_drawn"] = True

        # ツモ和了確認
        dealer = _current_dealer()
        winds = get_player_winds(dealer)
        tsumo_result = check_tsumo(
            GS["hands"]["Player"],
            is_riichi=GS["riichi"]["Player"],
            player_wind=winds["Player"],
            round_wind=get_round_info(GS["round_number"])["round_wind"],
            dora_indicators=GS.get("dora_indicators", [GS["dora_indicator"]]),
            melds=GS["melds"].get("Player"),
        )
        if tsumo_result:
            fmt = format_result(tsumo_result, is_tsumo=True)
            if GS["riichi"]["Player"]:
                winner_hand = list(GS["hands"]["Player"])
                GS["game_over"] = True
                GS["game_result"] = {
                    "type": "tsumo", "winner": "Player",
                    "tile": new_tile, "details": fmt,
                    "winner_hand": winner_hand,
                    "scores_before": dict(GS["scores"]),
                }
                apply_tsumo_points("Player", fmt)
                if GS["riichi_pot"] > 0:
                    GS["scores"]["Player"] += GS["riichi_pot"]
                    GS["riichi_pot"] = 0
                _finalize_and_save_log(GS["game_result"], GS["game_result"]["scores_before"])
                st.rerun()
            else:
                GS["tsumo_available"] = {
                    "result": tsumo_result, "fmt": fmt, "tile": new_tile,
                }
                st.rerun()
        else:
            if GS["riichi"]["Player"]:
                handle_player_discard(len(GS["hands"]["Player"]) - 1)
            else:
                st.rerun()

    # オートプレイ（find_best_discard で最適打牌）
    if auto_play and not GS["game_over"]:
        time.sleep(0.1)
        if GS["riichi"]["Player"]:
            handle_player_discard(len(GS["hands"]["Player"]) - 1)
        else:
            _ap_context = {
                "rivers": GS["rivers"], "scores": GS["scores"],
                "player": "Player", "wall_count": len(GS["wall"]),
                "riichi": GS["riichi"],
                "dora_indicators": GS.get("dora_indicators", [GS["dora_indicator"]]),
                "melds": GS["melds"].get("Player"),
            }
            best_idx, _ = find_best_discard(GS["hands"]["Player"], context=_ap_context)
            # リーチ判定（副露なし・未リーチ・1000点以上）
            if not GS["melds"]["Player"] and GS["scores"]["Player"] >= 1000:
                hand_after_ap = [t for i, t in enumerate(GS["hands"]["Player"]) if i != best_idx]
                if calculate_shanten(hand_after_ap) == 0:
                    GS["riichi"]["Player"] = True
                    GS["scores"]["Player"] -= 1000
                    GS["riichi_pot"] += 1000
            handle_player_discard(best_idx)

    # 手動打牌
    hand_len = len(GS["hands"]["Player"])
    if clicked > -1 and clicked < hand_len and not GS["game_over"] and not GS.get("tsumo_available"):
        if GS["riichi"]["Player"] and GS["has_drawn"]:
            last_idx = hand_len - 1
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
            scores_before_draw = dict(GS["scores"])
            _apply_tenpai_payments()
            GS["game_over"] = True
            GS["game_result"] = {"type": "exhaustive_draw", "scores_before": scores_before_draw}
            _finalize_and_save_log(GS["game_result"], scores_before_draw)
            st.rerun()
        GS["hands"][cp].append(GS["wall"].pop())
        GS["has_drawn"] = True
        st.rerun()
    else:
        handle_cpu_turn(cp)
