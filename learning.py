"""局面別の好手・悪手抽出と、検証付き重み学習。"""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from typing import Callable

import ai_weights
import game_log
import simulation
from decision_context import context_label

StageCallback = Callable[[str, int, int], None]


def _decisions(rounds: list[dict]) -> list[dict]:
    return [
        decision
        for round_record in rounds
        for decision in round_record.get("decisions", [])
        if decision.get("context_key")
    ]


def analyze_decision_contexts(rounds: list[dict]) -> list[dict]:
    """局面カテゴリごとの結果・安全度・進行度を集計する。"""
    grouped = defaultdict(list)
    for decision in _decisions(rounds):
        grouped[decision["context_key"]].append(decision)

    summaries = []
    for key, decisions in grouped.items():
        count = len(decisions)
        direct_losses = [
            decision for decision in decisions
            if decision.get("outcome") == "ron_dealt_last"
        ]
        draws = [decision for decision in decisions if decision.get("outcome") == "draw"]
        wins = [decision for decision in decisions if decision.get("outcome") == "won"]
        features = decisions[0].get("context", {})
        summaries.append({
            "context_key": key,
            "label": context_label(features),
            "features": features,
            "decisions": count,
            "direct_deal_ins": len(direct_losses),
            "deal_in_rate": round(len(direct_losses) / count * 100, 2),
            "draw_decision_rate": round(len(draws) / count * 100, 2),
            "win_decision_rate": round(len(wins) / count * 100, 2),
            "average_shanten": round(
                sum(decision.get("sh_after", 99) for decision in decisions) / count,
                2,
            ),
            "average_safety": round(
                sum(decision.get("safety_raw", 0) for decision in decisions) / count,
                2,
            ),
            "average_deal_in_safety": round(
                sum(decision.get("safety_raw", 0) for decision in direct_losses)
                / len(direct_losses),
                2,
            ) if direct_losses else 0.0,
        })
    summaries.sort(key=lambda item: (-item["direct_deal_ins"], -item["decisions"]))
    return summaries


def _best_safer_alternative(decision: dict) -> dict | None:
    chosen = decision.get("chosen", {})
    candidates = [
        candidate for candidate in decision.get("alternatives", [])
        if candidate.get("idx") != chosen.get("idx")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.get("safety_raw", 0))


def _example(decision: dict, kind: str, reason: str, severity: float) -> dict:
    alternative = _best_safer_alternative(decision)
    return {
        "kind": kind,
        "severity": round(severity, 1),
        "context_key": decision.get("context_key", ""),
        "context_label": context_label(decision.get("context", {})),
        "round": decision.get("round"),
        "turn": decision.get("turn"),
        "player": decision.get("player"),
        "hand": list(decision.get("hand", [])),
        "rivers": copy.deepcopy(decision.get("rivers", {})),
        "scores": dict(decision.get("scores", {})),
        "dora_indicators": list(decision.get("dora_indicators", [])),
        "chosen": copy.deepcopy(decision.get("chosen", {})),
        "alternative": copy.deepcopy(alternative),
        "outcome": decision.get("outcome"),
        "reason": reason,
    }


def extract_decision_examples(rounds: list[dict], limit: int = 12) -> dict[str, list[dict]]:
    """牌譜から、根拠を説明できる具体的な好手・悪手を抽出する。"""
    good = []
    bad = []
    for decision in _decisions(rounds):
        chosen = decision.get("chosen", {})
        safety = chosen.get("safety_raw", decision.get("safety_raw", 0)) or 0
        acceptance = chosen.get("weighted_acc", 0) or 0
        alternatives = decision.get("alternatives", [])
        safer = _best_safer_alternative(decision)

        if decision.get("outcome") == "ron_dealt_last":
            safer_score = safer.get("safety_raw", 0) if safer else 0
            safety_gap = max(0, safer_score - safety)
            if safety_gap >= 20:
                reason = (
                    f"安全度{safety:.0f}の{decision.get('chosen_tile')}で放銃。"
                    f"安全度{safer_score:.0f}の{safer.get('tile')}を選べました。"
                )
            else:
                reason = (
                    f"{decision.get('chosen_tile')}が放銃牌。"
                    "保存候補内にも明確な安全牌がありませんでした。"
                )
            bad.append(_example(decision, "bad", reason, 80 + safety_gap))
            continue

        regret = decision.get("regret", 0) or 0
        if regret >= 10 and not decision.get("self_riichi"):
            reason = (
                f"AI最上位候補との差が{regret:.1f}点。"
                f"{decision.get('chosen_tile')}より{decision.get('best_tile')}が有利でした。"
            )
            bad.append(_example(decision, "bad", reason, regret))

        minimum_shanten = min(
            (candidate.get("sh", 99) for candidate in alternatives),
            default=chosen.get("sh", 99),
        )
        maximum_acceptance = max(
            (candidate.get("weighted_acc", 0) or 0 for candidate in alternatives),
            default=acceptance,
        )
        under_pressure = decision.get("riichi_count", 0) > 0
        if under_pressure and safety >= 75 and decision.get("outcome") != "ron_dealt_last":
            good.append(_example(
                decision,
                "good",
                f"リーチを受け、安全度{safety:.0f}の牌を選択しました。",
                safety,
            ))
        elif (
            chosen.get("sh", 99) == minimum_shanten
            and acceptance == maximum_acceptance
            and decision.get("is_strictly_best")
        ):
            good.append(_example(
                decision,
                "good",
                f"シャンテンを維持し、候補中最大の受け入れ{acceptance}枚を確保しました。",
                40 + acceptance,
            ))

    good.sort(key=lambda item: -item["severity"])
    bad.sort(key=lambda item: -item["severity"])
    return {"good": good[:limit], "bad": bad[:limit]}


def propose_context_weights(
    rounds: list[dict],
    *,
    minimum_decisions: int = 20,
    base_weights: dict | None = None,
) -> dict:
    """局面カテゴリごとに、根拠付きの保守的な重み候補を作る。"""
    baseline = copy.deepcopy(base_weights or ai_weights.snapshot())
    candidate = copy.deepcopy(baseline)
    profiles = candidate.setdefault("context_profiles", {})
    changes = []

    for summary in analyze_decision_contexts(rounds):
        if summary["decisions"] < minimum_decisions:
            continue
        key = summary["context_key"]
        features = summary["features"]
        resolved = ai_weights.resolve(key, weights=baseline)
        profile = copy.deepcopy(profiles.get(key, {}))
        reasons = []

        if (
            features.get("pressure") != "no_riichi"
            and summary["direct_deal_ins"] >= 2
            and summary["average_deal_in_safety"] < 60
        ):
            profile["safety_base"] = round(min(0.25, resolved["safety_base"] + 0.02), 4)
            profile["safety_per_riichi"] = round(
                min(0.35, resolved["safety_per_riichi"] + 0.03), 4
            )
            reasons.append(
                f"放銃{summary['direct_deal_ins']}件、放銃牌平均安全度"
                f"{summary['average_deal_in_safety']:.1f}"
            )

        if (
            features.get("phase") in ("middle", "late")
            and features.get("pressure") == "no_riichi"
            and features.get("distance") == "two_plus"
            and summary["draw_decision_rate"] >= 55
        ):
            profile["acc_norm"] = round(max(16.0, resolved["acc_norm"] - 1.0), 2)
            reasons.append(
                f"流局局面比率{summary['draw_decision_rate']:.1f}%、"
                f"平均{summary['average_shanten']:.1f}シャンテン"
            )

        if profile and profile != profiles.get(key, {}):
            profiles[key] = profile
            changes.append({
                "context_key": key,
                "label": summary["label"],
                "profile": profile,
                "reasons": reasons,
                "sample_size": summary["decisions"],
            })

    return {
        "baseline_weights": baseline,
        "candidate_weights": candidate,
        "changes": changes,
        "context_summaries": analyze_decision_contexts(rounds),
    }


def _seed_set(seed: int, count: int) -> list[int]:
    source = random.Random(seed)
    return [source.randrange(0, 2**63) for _ in range(count)]


def _compare(baseline: dict, candidate: dict) -> dict:
    baseline_analysis = baseline["analysis"]
    candidate_analysis = candidate["analysis"]
    return {
        "baseline": baseline,
        "candidate": candidate,
        "score_gain": round(
            candidate["average_score_delta"] - baseline["average_score_delta"], 1
        ),
        "placement_gain": round(
            baseline["average_placement"] - candidate["average_placement"], 2
        ),
        "win_rate_gain": round(
            candidate_analysis.get("win_rate", 0) - baseline_analysis.get("win_rate", 0),
            1,
        ),
        "deal_in_rate_change": round(
            candidate_analysis.get("ron_dealt_rate", 0)
            - baseline_analysis.get("ron_dealt_rate", 0),
            1,
        ),
        "late_tenpai_rate_change": round(
            candidate_analysis.get("late_tenpai_rate", 0)
            - baseline_analysis.get("late_tenpai_rate", 0),
            1,
        ),
    }


def evaluate_context_candidate(
    proposal: dict,
    *,
    seed: int,
    training_rounds: int = 8,
    validation_rounds: int = 8,
    progress: StageCallback | None = None,
) -> dict:
    """同seed A/Bと未使用seed検証を行い、採用可否を返す。"""
    if not proposal.get("changes"):
        raise ValueError("proposal has no contextual changes")
    if training_rounds < 1 or validation_rounds < 1:
        raise ValueError("training_rounds and validation_rounds must be positive")

    baseline_weights = copy.deepcopy(proposal["baseline_weights"])
    candidate_weights = copy.deepcopy(proposal["candidate_weights"])
    baseline_policy = {player: baseline_weights for player in simulation.PLAYERS}
    candidate_policy = {
        player: candidate_weights if player == "Player" else baseline_weights
        for player in simulation.PLAYERS
    }
    training_seeds = _seed_set(seed, training_rounds)
    validation_seeds = _seed_set(seed ^ 0x5DEECE66D, validation_rounds)

    if progress:
        progress("training_baseline", 1, 4)
    training_baseline = simulation.run_seed_suite(
        training_seeds, weights_by_player=baseline_policy
    )
    if progress:
        progress("training_candidate", 2, 4)
    training_candidate = simulation.run_seed_suite(
        training_seeds, weights_by_player=candidate_policy
    )
    training = _compare(training_baseline, training_candidate)

    if progress:
        progress("validation_baseline", 3, 4)
    validation_baseline = simulation.run_seed_suite(
        validation_seeds, weights_by_player=baseline_policy
    )
    if progress:
        progress("validation_candidate", 4, 4)
    validation_candidate = simulation.run_seed_suite(
        validation_seeds, weights_by_player=candidate_policy
    )
    validation = _compare(validation_baseline, validation_candidate)

    training_passed = (
        training["score_gain"] > 0
        and training["placement_gain"] > 0
        and training["deal_in_rate_change"] <= 0
        and training["late_tenpai_rate_change"] >= 0
    )
    validation_passed = (
        validation["score_gain"] > 0
        and validation["placement_gain"] > 0
        and validation["deal_in_rate_change"] <= 0
        and validation["late_tenpai_rate_change"] >= 0
    )
    return {
        "seed": seed,
        "training_seeds": training_seeds,
        "validation_seeds": validation_seeds,
        "training": training,
        "validation": validation,
        "training_passed": training_passed,
        "validation_passed": validation_passed,
        "accepted": training_passed and validation_passed,
        "baseline_weights": baseline_weights,
        "candidate_weights": candidate_weights,
        "changes": copy.deepcopy(proposal["changes"]),
    }


def apply_validated_candidate(evaluation: dict) -> dict | None:
    """学習・未使用seedの両方を通過した候補だけを永続化する。"""
    if not all(
        evaluation.get(flag)
        for flag in ("training_passed", "validation_passed", "accepted")
    ):
        return None
    if ai_weights.snapshot() != evaluation.get("baseline_weights"):
        raise RuntimeError("AI weights changed after evaluation; run A/B evaluation again")
    candidate = evaluation["candidate_weights"]
    difference = {
        "context_profiles": copy.deepcopy(candidate.get("context_profiles", {}))
    }
    validation_analysis = evaluation["validation"]["candidate"]["analysis"]
    return game_log.apply_tuning(
        difference,
        analysis=validation_analysis,
        source=f"validated-context:{evaluation['seed']}",
    )
