"""
牌譜管理・分析・AIパラメータチューニングモジュール

- 打牌決定をJSONL形式で記録
- 局終了時に結果を紐付け（振り込み・和了・流局）
- ログ集計でAIの弱点を特定
- ai_weights を自動調整して改善をフィードバック
"""
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

import ai_weights as _aw

_DATA_DIR = Path(os.environ.get("MAHJONG_DATA_DIR", Path(__file__).parent))
LOG_FILE = _DATA_DIR / "game_logs.jsonl"
ANALYSIS_FILE = _DATA_DIR / "analysis_cache.json"
TUNING_HISTORY_FILE = _DATA_DIR / "ai_tuning_history.jsonl"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# チューニングに必要な最低局数
_MIN_ROUNDS_FOR_TUNING = 20


# =============================================================================
# 打牌決定レコード生成
# =============================================================================

def _compact_candidate(candidate: dict) -> dict:
    return {
        key: candidate.get(key)
        for key in (
            "idx", "tile", "label", "sh", "weighted_acc", "safety_raw",
            "sh_score", "acc_score", "dora_score", "safety_score",
            "situation_score", "raw_total", "total_score",
        )
    }


def _decision_snapshot(
    hand_14: list,
    chosen: dict,
    candidates: list,
    context: dict,
) -> dict:
    recorded_candidates = list(candidates[:5])
    if candidates:
        safest = max(candidates, key=lambda candidate: candidate.get("safety_raw", 0))
        if all(
            candidate.get("idx") != safest.get("idx")
            for candidate in recorded_candidates
        ):
            recorded_candidates.append(safest)
    return {
        "hand": list(hand_14),
        "rivers": {
            player: list(river)
            for player, river in (context.get("rivers", {}) or {}).items()
        },
        "scores": dict(context.get("scores", {}) or {}),
        "riichi": dict(context.get("riichi", {}) or {}),
        "dora_indicators": list(context.get("dora_indicators", []) or []),
        "self_riichi": bool(
            (context.get("riichi", {}) or {}).get(context.get("player"), False)
        ),
        "context_key": chosen.get("context_key", ""),
        "context": dict(chosen.get("context", {}) or {}),
        "chosen": _compact_candidate(chosen),
        "alternatives": [
            _compact_candidate(candidate) for candidate in recorded_candidates
        ],
    }


def is_best_move(eval_result: dict) -> bool:
    """AI最善手と同点（100点）の打牌も最善手として扱う。"""
    if eval_result.get("is_strictly_best", False):
        return True
    if "total_score" not in eval_result:
        return False
    return eval_result["total_score"] >= eval_result.get("best_score", 100.0)


def count_best_moves(eval_results: list) -> int:
    return sum(1 for eval_result in eval_results if is_best_move(eval_result))


def make_player_decision(
    round_number: int,
    turn_count: int,
    hand_14: list,
    chosen_idx: int,
    eval_result: dict,
    context: dict,
) -> dict:
    """プレイヤー判断を、局面再現用スナップショットとともに記録する。"""
    candidates = eval_result.get("all_candidates", [])
    chosen = next(
        (candidate for candidate in candidates if candidate.get("idx") == chosen_idx),
        {},
    )
    snapshot = _decision_snapshot(hand_14, chosen, candidates, context)
    return {
        **snapshot,
        "policy": "human",
        "player": "Player",
        "round": round_number,
        "turn": turn_count,
        "wall_count": context.get("wall_count", 0),
        "riichi_count": sum(
            1 for player, declared in context.get("riichi", {}).items()
            if declared and player != "Player"
        ),
        "chosen_idx": chosen_idx,
        "chosen_tile": eval_result.get("player_tile", ""),
        "chosen_score": round(eval_result.get("total_score", 0), 2),
        "best_tile": eval_result.get("best_tile", ""),
        "best_score": 100.0,
        "regret": round(100.0 - eval_result.get("total_score", 100), 2),
        "is_strictly_best": eval_result.get("is_strictly_best", False),
        "sh_after": eval_result.get("player_shanten", 99),
        "comp_sh": eval_result.get("player_sh_score", 0),
        "comp_acc": eval_result.get("player_acc_score", 0),
        "comp_dora": eval_result.get("player_dora_score", 0),
        "comp_safety": eval_result.get("player_safety_score", 0),
        "safety_raw": chosen.get("safety_raw", 0),
        "comp_situation": eval_result.get("player_situation_score", 0),
        "best_comp_sh": candidates[0].get("sh_score", 0) if candidates else 0,
        "best_comp_acc": candidates[0].get("acc_score", 0) if candidates else 0,
        "best_comp_dora": candidates[0].get("dora_score", 0) if candidates else 0,
        "outcome": None,
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
    """AI判断を、候補比較と局面再現用スナップショット付きで記録する。"""
    if not candidates:
        return {}
    best = candidates[0]
    chosen = next(
        (candidate for candidate in candidates if candidate.get("idx") == chosen_idx),
        best,
    )
    chosen_score = chosen.get("total_score", 100.0)
    snapshot = _decision_snapshot(hand_14, chosen, candidates, context)
    return {
        **snapshot,
        "policy": "ai",
        "player": player,
        "round": round_number,
        "turn": turn_count,
        "wall_count": context.get("wall_count", 0),
        "riichi_count": sum(
            1 for name, declared in context.get("riichi", {}).items()
            if declared and name != player
        ),
        "chosen_idx": chosen_idx,
        "chosen_tile": hand_14[chosen_idx] if chosen_idx < len(hand_14) else "",
        "chosen_score": round(chosen_score, 2),
        "best_tile": best.get("tile", ""),
        "best_score": 100.0,
        "regret": round(100.0 - chosen_score, 2),
        "is_strictly_best": chosen_idx == best.get("idx", -1),
        "sh_after": chosen.get("sh", 99),
        "comp_sh": chosen.get("sh_score", 0),
        "comp_acc": chosen.get("acc_score", 0),
        "comp_dora": chosen.get("dora_score", 0),
        "comp_safety": chosen.get("safety_score", 0),
        "safety_raw": chosen.get("safety_raw", 0),
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
    metadata: dict | None = None,
) -> dict | None:
    """1局分のログをJSONLへ追記し、保存したレコードを返す。"""
    if not decision_log:
        return None
    record = {
        "ts": time.time(),
        "round": round_number,
        "result_type": game_result.get("type", "unknown"),
        "winner": game_result.get("winner", ""),
        "loser": game_result.get("loser", ""),
        "scores_before": scores_before,
        "tenpai_players": game_result.get("tenpai_players", []),
        "riichi_pot_after": game_result.get("riichi_pot_after", 0),
        "scores_after": scores_after,
        "decisions": decision_log,
        "metadata": metadata or {},
    }
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_logs(max_rounds: int = 200) -> list:
    """ファイル全体をメモリへ載せず、直近N局分のログを読み込む。"""
    if not LOG_FILE.exists() or max_rounds <= 0:
        return []
    recent_lines = deque(maxlen=max_rounds)
    with LOG_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                recent_lines.append(line)
    rounds = []
    for line in recent_lines:
        try:
            rounds.append(json.loads(line))
        except (TypeError, json.JSONDecodeError):
            continue
    return rounds

def load_logs_for_run(run_id: str, max_rounds: int = 500) -> list:
    """指定Runの牌譜だけを、件数上限付きで読み込む。"""
    if not LOG_FILE.exists() or not run_id or max_rounds <= 0:
        return []
    matching = deque(maxlen=max_rounds)
    with LOG_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("metadata", {}).get("run_id") == run_id:
                matching.append(record)
    return list(matching)


def clear_logs() -> None:
    """ログをリセットする"""
    if LOG_FILE.exists():
        LOG_FILE.unlink()


# =============================================================================
# 分析
# =============================================================================

def analyze(rounds: list, focus_player: str | None = None) -> dict:
    """対局結果と局面別の判断を集計し、改善点を診断する。"""
    if not rounds:
        return {}

    outcomes = defaultdict(int)
    result_types = defaultdict(int)
    player_decisions = []
    all_decisions = []
    score_changes = []

    for round_record in rounds:
        result_type = round_record.get("result_type", "unknown")
        winner = round_record.get("winner", "")
        loser = round_record.get("loser", "")
        result_types[result_type] += 1
        if result_type == "exhaustive_draw":
            outcomes["draw"] += 1
        elif winner == (focus_player or "Player"):
            outcomes["win"] += 1
        elif (
            loser == (focus_player or "Player")
            or (result_type == "tsumo" and winner != (focus_player or "Player"))
        ):
            outcomes["lose"] += 1
        else:
            outcomes["other"] += 1

        before = round_record.get("scores_before", {})
        after = round_record.get("scores_after", {})
        tracked_player = focus_player or "Player"
        if tracked_player in before and tracked_player in after:
            score_changes.append(after[tracked_player] - before[tracked_player])

        for decision in round_record.get("decisions", []):
            all_decisions.append(decision)
            if decision.get("player") == "Player":
                player_decisions.append(decision)

    total = len(rounds)
    if focus_player:
        focused = [
            decision for decision in all_decisions
            if decision.get("player") == focus_player
        ]
        evaluated_decisions = [
            decision for decision in focused if decision.get("policy") == "ai"
        ] or focused
        ai_decisions = [
            decision for decision in all_decisions
            if decision.get("policy") == "ai"
        ]
    else:
        ai_decisions = [
            decision for decision in all_decisions if decision.get("policy") == "ai"
        ]
        evaluated_decisions = ai_decisions or player_decisions
    riichi_decisions = [
        decision for decision in evaluated_decisions
        if decision.get("riichi_count", 0) > 0
    ]
    normal_decisions = [
        decision for decision in evaluated_decisions
        if decision.get("riichi_count", 0) == 0
    ]
    late_decisions = [
        decision for decision in evaluated_decisions
        if decision.get("wall_count", 70) <= 30
    ]
    direct_deal_ins = [
        decision for decision in evaluated_decisions
        if decision.get("outcome") == "ron_dealt_last"
    ]

    avg_regret = _mean([decision.get("regret", 0) for decision in evaluated_decisions])
    riichi_regret = _mean([decision.get("regret", 0) for decision in riichi_decisions])
    normal_regret = _mean([decision.get("regret", 0) for decision in normal_decisions])
    optimal_rate = (
        sum(1 for decision in evaluated_decisions if decision.get("is_strictly_best", False))
        / len(evaluated_decisions)
        * 100
        if evaluated_decisions else 0
    )
    late_tenpai_rate = (
        sum(1 for decision in late_decisions if decision.get("sh_after", 99) <= 0)
        / len(late_decisions)
        * 100
        if late_decisions else 0
    )
    average_safety = _mean([
        decision.get("safety_raw", decision.get("comp_safety", 0))
        for decision in evaluated_decisions
    ])
    deal_in_safety = _mean([
        decision.get("safety_raw", decision.get("comp_safety", 0))
        for decision in direct_deal_ins
    ])

    comp_regret = {}
    for component in ("sh", "acc", "dora", "safety"):
        differences = [
            decision.get(f"best_comp_{component}", 0)
            - decision.get(f"comp_{component}", 0)
            for decision in evaluated_decisions
            if not decision.get("is_strictly_best", True)
        ]
        comp_regret[component] = round(_mean(differences), 2)

    draw_rate = result_types["exhaustive_draw"] / total * 100
    deal_in_rate = len(direct_deal_ins) / total * 100
    diagnostics = _build_diagnostics(
        total_rounds=total,
        draw_rate=draw_rate,
        deal_in_rate=deal_in_rate,
        late_tenpai_rate=late_tenpai_rate,
        optimal_rate=optimal_rate,
    )

    return {
        "total_rounds": total,
        "total_player_decisions": len(player_decisions),
        "total_ai_decisions": len(ai_decisions),
        "total_evaluated_decisions": len(evaluated_decisions),
        "win_rate": round(outcomes["win"] / total * 100, 1),
        "draw_rate": round(draw_rate, 1),
        "lose_rate": round(outcomes["lose"] / total * 100, 1),
        "tsumo_rate": round(result_types["tsumo"] / total * 100, 1),
        "ron_rate": round(result_types["ron"] / total * 100, 1),
        "avg_score_change": round(_mean(score_changes), 1),
        "avg_regret": round(avg_regret, 2),
        "riichi_regret": round(riichi_regret, 2),
        "normal_regret": round(normal_regret, 2),
        "ron_dealt_rate": round(deal_in_rate, 1),
        "ron_avoidable": sum(
            1 for decision in direct_deal_ins if decision.get("regret", 0) > 5
        ),
        "optimal_rate": round(optimal_rate, 1),
        "late_tenpai_rate": round(late_tenpai_rate, 1),
        "avg_late_shanten": round(_mean([
            decision.get("sh_after", 99) for decision in late_decisions
        ]), 2),
        "avg_safety": round(average_safety, 2),
        "avg_deal_in_safety": round(deal_in_safety, 2),
        "comp_regret": comp_regret,
        "outcomes": dict(outcomes),
        "result_types": dict(result_types),
        "diagnostics": diagnostics,
    }


def _build_diagnostics(
    total_rounds: int,
    draw_rate: float,
    deal_in_rate: float,
    late_tenpai_rate: float,
    optimal_rate: float,
) -> list[dict]:
    diagnostics = []
    if total_rounds < _MIN_ROUNDS_FOR_TUNING:
        diagnostics.append({
            "status": "info",
            "title": "データ不足",
            "detail": f"安定した調整には最低{_MIN_ROUNDS_FOR_TUNING}局必要です。",
        })

    if deal_in_rate > 20:
        diagnostics.append({
            "status": "bad",
            "title": "放銃が多い",
            "detail": f"放銃局率{deal_in_rate:.1f}%。リーチ時の安全度を強める余地があります。",
        })
    elif deal_in_rate <= 12:
        diagnostics.append({
            "status": "good",
            "title": "守備は安定",
            "detail": f"放銃局率{deal_in_rate:.1f}%に抑えられています。",
        })

    if late_tenpai_rate < 35:
        diagnostics.append({
            "status": "bad",
            "title": "終盤の聴牌率が低い",
            "detail": f"終盤聴牌率{late_tenpai_rate:.1f}%。受け入れ評価の強化候補です。",
        })
    elif late_tenpai_rate >= 50:
        diagnostics.append({
            "status": "good",
            "title": "牌効率は良好",
            "detail": f"終盤聴牌率{late_tenpai_rate:.1f}%を確保しています。",
        })

    if draw_rate > 55:
        diagnostics.append({
            "status": "bad",
            "title": "流局が多い",
            "detail": f"流局率{draw_rate:.1f}%。手作り速度を見直す余地があります。",
        })
    if optimal_rate >= 95:
        diagnostics.append({
            "status": "good",
            "title": "評価関数との整合性",
            "detail": f"最善候補選択率{optimal_rate:.1f}%です。",
        })
    return diagnostics


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


# =============================================================================
# パラメータ調整（チューニング）
# =============================================================================

def suggest_tuning(analysis: dict) -> tuple[dict, list[str]]:
    """結果指標から、1回で振れすぎない保守的な重み差分を作る。"""
    round_count = analysis.get("total_rounds", 0) if analysis else 0
    if round_count < _MIN_ROUNDS_FOR_TUNING:
        return {}, [
            f"チューニングには最低 {_MIN_ROUNDS_FOR_TUNING} 局必要です"
            f"（現在: {round_count} 局）"
        ]

    weights = _aw.get().copy()
    notes = []
    deal_in_rate = analysis.get("ron_dealt_rate", 0)
    draw_rate = analysis.get("draw_rate", 0)
    late_tenpai_rate = analysis.get("late_tenpai_rate", 0)
    average_safety = analysis.get("avg_safety", 0)
    deal_in_safety = analysis.get("avg_deal_in_safety", 0)

    if deal_in_rate > 20 and deal_in_safety <= average_safety:
        weights["safety_base"] = round(
            min(0.20, weights["safety_base"] + 0.01), 4
        )
        weights["safety_per_riichi"] = round(
            min(0.25, weights["safety_per_riichi"] + 0.02), 4
        )
        notes.append(
            f"放銃局率 {deal_in_rate:.1f}% → 安全度 base +0.01 / riichi +0.02"
        )
    elif deal_in_rate < 10 and draw_rate > 55:
        weights["safety_base"] = round(
            max(0.02, weights["safety_base"] - 0.01), 4
        )
        notes.append(
            f"放銃局率 {deal_in_rate:.1f}%・流局率 {draw_rate:.1f}%"
            " → 守備の基本重み -0.01"
        )

    if draw_rate > 55 and late_tenpai_rate < 40:
        weights["acc_norm"] = round(max(16.0, weights["acc_norm"] - 1.0), 2)
        notes.append(
            f"流局率 {draw_rate:.1f}%・終盤聴牌率 {late_tenpai_rate:.1f}%"
            " → 受け入れ正規化 -1.0"
        )

    if not notes:
        notes.append("今回のデータでは重み変更を推奨しません。")

    current = _aw.get()
    differences = {
        key: value for key, value in weights.items() if value != current.get(key)
    }
    return differences, notes


def apply_tuning(
    weight_diff: dict,
    analysis: dict | None = None,
    source: str = "manual",
) -> dict | None:
    """調整を保存し、変更前後と根拠を監査ログへ残す。"""
    if not weight_diff:
        return None
    before = json.loads(json.dumps(_aw.get()))
    _aw.update(weight_diff)
    _aw.save()
    after = json.loads(json.dumps(_aw.get()))
    record = {
        "ts": time.time(),
        "source": source,
        "before": before,
        "after": after,
        "diff": weight_diff,
        "metrics": {
            key: analysis.get(key)
            for key in (
                "total_rounds", "draw_rate", "ron_dealt_rate",
                "late_tenpai_rate", "optimal_rate",
            )
        } if analysis else {},
    }
    with TUNING_HISTORY_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_tuning_history(max_entries: int = 20) -> list:
    if not TUNING_HISTORY_FILE.exists() or max_entries <= 0:
        return []
    records = deque(maxlen=max_entries)
    with TUNING_HISTORY_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(records)


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
