"""
牌譜管理・分析・AIパラメータチューニングモジュール

- 打牌決定をJSONL形式で記録
- 局終了時に結果を紐付け（振り込み・和了・流局）
- ログ集計でAIの弱点を特定
- ai_weights を自動調整して改善をフィードバック
"""
import json
import time
from collections import defaultdict
from pathlib import Path

import ai_weights as _aw

LOG_FILE = Path(__file__).parent / "game_logs.jsonl"
ANALYSIS_FILE = Path(__file__).parent / "analysis_cache.json"

# チューニング時の調整ステップ幅（保守的な値）
_TUNE_STEP = 0.03
# チューニングに必要な最低局数
_MIN_ROUNDS_FOR_TUNING = 10


# =============================================================================
# 打牌決定レコード生成
# =============================================================================

def make_player_decision(
    round_number: int,
    turn_count: int,
    hand_14: list,
    chosen_idx: int,
    eval_result: dict,
    context: dict,
) -> dict:
    """
    evaluate_discard の結果から Player の打牌レコードを生成。
    eval_result には all_candidates が含まれている必要がある。
    """
    return {
        "player": "Player",
        "round": round_number,
        "turn": turn_count,
        "wall_count": context.get("wall_count", 0),
        "riichi_count": sum(
            1 for p, r in context.get("riichi", {}).items()
            if r and p != "Player"
        ),
        "chosen_idx": chosen_idx,
        "chosen_tile": eval_result.get("player_tile", ""),
        "chosen_score": round(eval_result.get("total_score", 0), 2),
        "best_tile": eval_result.get("best_tile", ""),
        "best_score": 100.0,
        "regret": round(100.0 - eval_result.get("total_score", 100), 2),
        "is_strictly_best": eval_result.get("is_strictly_best", False),
        "sh_after": eval_result.get("player_shanten", 99),
        # コンポーネントスコア（チューニング用）
        "comp_sh": eval_result.get("player_sh_score", 0),
        "comp_acc": eval_result.get("player_acc_score", 0),
        "comp_dora": eval_result.get("player_dora_score", 0),
        "comp_safety": eval_result.get("player_safety_score", 0),
        "comp_situation": eval_result.get("player_situation_score", 0),
        "best_comp_sh": eval_result.get("all_candidates", [{}])[0].get("sh_score", 0) if eval_result.get("all_candidates") else 0,
        "best_comp_acc": eval_result.get("all_candidates", [{}])[0].get("acc_score", 0) if eval_result.get("all_candidates") else 0,
        "best_comp_dora": eval_result.get("all_candidates", [{}])[0].get("dora_score", 0) if eval_result.get("all_candidates") else 0,
        "outcome": None,  # 局終了時に付与
    }


def make_cpu_decision(
    round_number: int,
    turn_count: int,
    player: str,
    hand_14: list,
    chosen_idx: int,
    candidates: list,
    context: dict,
) -> dict:
    """
    find_best_discard + evaluate_hand_candidates の結果から CPU の打牌レコードを生成。
    candidates は _evaluate_hand_score の出力（total_score 付き）。
    """
    if not candidates:
        return {}
    best = candidates[0]
    chosen = next((c for c in candidates if c.get("idx") == chosen_idx), best)
    chosen_score = chosen.get("total_score", 100.0)
    return {
        "player": player,
        "round": round_number,
        "turn": turn_count,
        "wall_count": context.get("wall_count", 0),
        "riichi_count": sum(
            1 for p, r in context.get("riichi", {}).items()
            if r and p != player
        ),
        "chosen_idx": chosen_idx,
        "chosen_tile": hand_14[chosen_idx] if chosen_idx < len(hand_14) else "",
        "chosen_score": round(chosen_score, 2),
        "best_tile": best.get("tile", ""),
        "best_score": 100.0,
        "regret": round(100.0 - chosen_score, 2),
        "is_strictly_best": (chosen_idx == best.get("idx", -1)),
        "sh_after": chosen.get("sh", 99),
        "comp_sh": chosen.get("sh_score", 0),
        "comp_acc": chosen.get("acc_score", 0),
        "comp_dora": chosen.get("dora_score", 0),
        "comp_safety": chosen.get("safety_score", 0),
        "comp_situation": chosen.get("situation_score", 0),
        "best_comp_sh": best.get("sh_score", 0),
        "best_comp_acc": best.get("acc_score", 0),
        "best_comp_dora": best.get("dora_score", 0),
        "outcome": None,
    }


# =============================================================================
# 局終了処理
# =============================================================================

def finalize_round(
    decision_log: list,
    game_result: dict,
    scores_before: dict,
    scores_after: dict,
) -> None:
    """
    局終了時にすべての打牌レコードに outcome タグを付与する。
    - "won": 和了
    - "ron_dealt": 振り込み
    - "tsumo_dealt": 相手にツモ和了された
    - "draw": 流局
    """
    result_type = game_result.get("type", "unknown")
    winner = game_result.get("winner", "")
    loser = game_result.get("loser", "")

    for record in decision_log:
        p = record.get("player", "")
        if result_type == "ron":
            if p == winner:
                record["outcome"] = "won"
            elif p == loser:
                record["outcome"] = "ron_dealt"
            else:
                record["outcome"] = "tsumo_dealt"  # 直接放銃ではないが点を失う
        elif result_type == "tsumo":
            if p == winner:
                record["outcome"] = "won"
            else:
                record["outcome"] = "tsumo_dealt"
        else:
            record["outcome"] = "draw"

    # 放銃した打牌を特定（最後に捨てた牌がロン牌）
    if result_type == "ron" and loser:
        for record in reversed(decision_log):
            if record.get("player") == loser:
                record["outcome"] = "ron_dealt_last"  # 直接放銃した一打
                break


# =============================================================================
# ログ保存・読み込み
# =============================================================================

def save_round_log(
    round_number: int,
    decision_log: list,
    game_result: dict,
    scores_before: dict,
    scores_after: dict,
) -> None:
    """1局分のログをJSONLファイルに追記する"""
    if not decision_log:
        return
    record = {
        "ts": time.time(),
        "round": round_number,
        "result_type": game_result.get("type", "unknown"),
        "winner": game_result.get("winner", ""),
        "loser": game_result.get("loser", ""),
        "scores_before": scores_before,
        "scores_after": scores_after,
        "decisions": decision_log,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_logs(max_rounds: int = 200) -> list:
    """直近N局分のログを読み込む"""
    if not LOG_FILE.exists():
        return []
    lines = LOG_FILE.read_text(encoding="utf-8").strip().split("\n")
    lines = [ln for ln in lines if ln.strip()]
    rounds = []
    for line in lines[-max_rounds:]:
        try:
            rounds.append(json.loads(line))
        except Exception:
            pass
    return rounds


def clear_logs() -> None:
    """ログをリセットする"""
    if LOG_FILE.exists():
        LOG_FILE.unlink()


# =============================================================================
# 分析
# =============================================================================

def analyze(rounds: list) -> dict:
    """
    ログを解析してAI改善のための指標を計算する。

    主な指標:
    - 勝率 / 流局率 / 失点率
    - 平均regret（最善手との乖離）
    - リーチ局面でのregret（守備力の評価）
    - 放銃率・回避可能だった放銃の割合
    - コンポーネント別のregret（どの評価軸が弱いか）
    """
    if not rounds:
        return {}

    outcomes = defaultdict(int)
    player_decisions = []
    all_decisions = []

    for r in rounds:
        rt = r.get("result_type", "unknown")
        winner = r.get("winner", "")
        loser = r.get("loser", "")

        if rt == "exhaustive_draw":
            outcomes["draw"] += 1
        elif winner == "Player":
            outcomes["win"] += 1
        elif loser == "Player" or (rt == "tsumo" and winner != "Player"):
            outcomes["lose"] += 1
        else:
            outcomes["other"] += 1

        for d in r.get("decisions", []):
            all_decisions.append(d)
            if d.get("player") == "Player":
                player_decisions.append(d)

    total = len(rounds)
    if total == 0:
        return {}

    win_rate = outcomes["win"] / total * 100
    draw_rate = outcomes["draw"] / total * 100
    lose_rate = outcomes["lose"] / total * 100

    # プレイヤー打牌の平均regret
    pd = player_decisions
    avg_regret = _mean([d.get("regret", 0) for d in pd]) if pd else 0

    # リーチ者ありのとき vs なしのとき
    riichi_decisions = [d for d in pd if d.get("riichi_count", 0) > 0]
    normal_decisions = [d for d in pd if d.get("riichi_count", 0) == 0]
    riichi_regret = _mean([d.get("regret", 0) for d in riichi_decisions])
    normal_regret = _mean([d.get("regret", 0) for d in normal_decisions])

    # 放銃率
    ron_dealt_count = sum(1 for d in pd if d.get("outcome") in ("ron_dealt", "ron_dealt_last"))
    ron_dealt_rate = ron_dealt_count / total * 100

    # 放銃した打牌のうち、もっとよい牌があった割合
    ron_with_regret = sum(
        1 for d in pd
        if d.get("outcome") in ("ron_dealt", "ron_dealt_last") and d.get("regret", 0) > 5
    )

    # コンポーネント別 regret（どの評価軸が誤差を生んでいるか）
    # = best_comp_X - comp_X の平均（正 = AIはこの評価軸を過小評価している）
    comp_regret = {}
    for comp in ("sh", "acc", "dora", "safety"):
        diffs = [
            d.get(f"best_comp_{comp}", 0) - d.get(f"comp_{comp}", 0)
            for d in pd if not d.get("is_strictly_best", True)
        ]
        comp_regret[comp] = round(_mean(diffs), 2)

    # 最善手一致率
    optimal_rate = sum(1 for d in pd if d.get("is_strictly_best", False)) / len(pd) * 100 if pd else 0

    return {
        "total_rounds": total,
        "total_player_decisions": len(pd),
        "win_rate": round(win_rate, 1),
        "draw_rate": round(draw_rate, 1),
        "lose_rate": round(lose_rate, 1),
        "avg_regret": round(avg_regret, 2),
        "riichi_regret": round(riichi_regret, 2),
        "normal_regret": round(normal_regret, 2),
        "ron_dealt_rate": round(ron_dealt_rate, 1),
        "ron_avoidable": ron_with_regret,
        "optimal_rate": round(optimal_rate, 1),
        "comp_regret": comp_regret,
        "outcomes": dict(outcomes),
    }


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


# =============================================================================
# パラメータ調整（チューニング）
# =============================================================================

def suggest_tuning(analysis: dict) -> tuple[dict, list[str]]:
    """
    分析結果から重みの調整案を生成する。

    戻り値: (new_weights_dict, notes_list)
    - new_weights_dict: 変更後の重み（_aw.update() に渡す）
    - notes_list: 調整の説明リスト
    """
    if not analysis or analysis.get("total_rounds", 0) < _MIN_ROUNDS_FOR_TUNING:
        return {}, [f"チューニングには最低 {_MIN_ROUNDS_FOR_TUNING} 局必要です（現在: {analysis.get('total_rounds', 0)} 局）"]

    w = _aw.get().copy()
    notes = []

    win_rate = analysis.get("win_rate", 0)
    draw_rate = analysis.get("draw_rate", 0)
    lose_rate = analysis.get("lose_rate", 0)
    ron_dealt_rate = analysis.get("ron_dealt_rate", 0)
    ron_avoidable = analysis.get("ron_avoidable", 0)
    riichi_regret = analysis.get("riichi_regret", 0)
    normal_regret = analysis.get("normal_regret", 0)
    comp_regret = analysis.get("comp_regret", {})
    optimal_rate = analysis.get("optimal_rate", 0)

    # ① 放銃率が高く、リーチ局面でregretも大きい → 守備重みを増やす
    if ron_dealt_rate > 20 and ron_avoidable > 0 and riichi_regret > 8:
        adj = _TUNE_STEP
        w["safety_base"] = round(min(0.45, w["safety_base"] + adj), 4)
        w["safety_per_riichi"] = round(min(0.20, w["safety_per_riichi"] + adj * 0.5), 4)
        notes.append(
            f"放銃率 {ron_dealt_rate:.0f}%・リーチ局面regret {riichi_regret:.1f} → "
            f"safety_base +{adj:.3f}, safety_per_riichi +{adj*0.5:.3f}"
        )

    # ② 流局が多く、通常局面のregretは低い（最善手は選べているが勝てない）
    #    → 受け入れ枚数の評価を強化（攻め気を上げる）
    if draw_rate > 55 and normal_regret < 5 and comp_regret.get("acc", 0) > 2:
        adj = 2.0
        w["acc_norm"] = round(max(16.0, w["acc_norm"] - adj), 2)  # 分母を下げると得点が上がる
        notes.append(
            f"流局率 {draw_rate:.0f}%・受け入れ軸の過小評価検出 → acc_norm -{adj:.1f}"
        )

    # ③ ドラを不必要に捨てている（dora comp_regret が正 = ドラを過小評価）
    if comp_regret.get("dora", 0) > 3:
        w["dora_per_tile"] = round(min(9.0, w["dora_per_tile"] + 0.5), 2)
        notes.append(
            f"ドラ評価の過小評価検出（comp_regret={comp_regret.get('dora',0):.1f}）"
            f" → dora_per_tile +0.5"
        )

    # ④ 守りすぎ（リーチ局面regretが低いのに流局多い）→ 守備重みを微減
    if riichi_regret < 3 and draw_rate > 45 and win_rate < 20:
        adj = _TUNE_STEP * 0.5
        w["safety_base"] = round(max(0.08, w["safety_base"] - adj), 4)
        notes.append(
            f"流局多いがリーチ局面は適切に処理 → safety_base -{adj:.3f}（守り過剰を緩和）"
        )

    # ⑤ 最善手一致率が低い → テンパイ・リーチボーナスを見直し
    if optimal_rate < 60 and comp_regret.get("sh", 0) > 5:
        w["tenpai_bonus"] = round(min(8.0, w["tenpai_bonus"] + 0.5), 2)
        notes.append(
            f"シャンテン軸のregret大（{comp_regret.get('sh',0):.1f}）"
            f" → tenpai_bonus +0.5"
        )

    if not notes:
        notes.append("現在の重みは良好です（大きな調整は不要）")

    # _aw.update() に渡す差分のみ返す
    current = _aw.get()
    diff = {k: v for k, v in w.items() if v != current.get(k)}
    return diff, notes


def apply_tuning(weight_diff: dict) -> None:
    """suggest_tuning の結果を ai_weights に適用して保存"""
    if weight_diff:
        _aw.update(weight_diff)
        _aw.save()


def save_analysis_cache(analysis: dict) -> None:
    ANALYSIS_FILE.write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_analysis_cache() -> dict:
    if ANALYSIS_FILE.exists():
        try:
            return json.loads(ANALYSIS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
