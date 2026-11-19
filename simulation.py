"""UI rerunや待機を使わず、AI同士の半荘を連続実行する。"""

from __future__ import annotations

import random
import time
import uuid
from collections import Counter
from typing import Callable

import ai_weights
import game_log
from game_logic import (
    _evaluate_hand_score,
    check_ron,
    check_tsumo,
    format_result,
    is_tenpai,
)

PLAYERS = ("Player", "CPU1", "CPU2", "CPU3")
ProgressCallback = Callable[[int, int], None]


def _build_round(rng: random.Random, dealer: str) -> dict:
    tiles = [
        f"{number}{suit}"
        for suit in "mps"
        for number in range(1, 10)
        for _ in range(4)
    ] + [f"{number}z" for number in range(1, 8) for _ in range(4)]
    rng.shuffle(tiles)
    dead_wall = tiles[-14:]
    live_wall = tiles[:-14]
    hands = {player: [live_wall.pop() for _ in range(13)] for player in PLAYERS}
    hands[dealer].append(live_wall.pop())
    return {
        "wall": live_wall,
        "hands": hands,
        "rivers": {player: [] for player in PLAYERS},
        "riichi": {player: False for player in PLAYERS},
        "dora_indicators": [dead_wall[4]],
        "turn": dealer,
        "has_drawn": True,
        "turn_count": 0,
        "decision_log": [],
    }


def _player_winds(dealer: str) -> dict[str, int]:
    dealer_index = PLAYERS.index(dealer)
    winds = (27, 28, 29, 30)
    return {
        PLAYERS[(dealer_index + offset) % 4]: winds[offset]
        for offset in range(4)
    }


def _round_wind(round_number: int) -> int:
    return 27 if round_number <= 4 else 28


def _apply_tsumo(scores: dict[str, int], winner: str, dealer: str, details: dict) -> None:
    main = details["main_points"]
    additional = details["additional_points"]
    for player in PLAYERS:
        if player == winner:
            continue
        payment = additional if winner == dealer or player != dealer else main
        scores[player] -= payment
        scores[winner] += payment


def _apply_tenpai_payments(scores: dict[str, int], hands: dict[str, list[str]]) -> list[str]:
    tenpai_players = [player for player in PLAYERS if is_tenpai(hands[player])]
    if not tenpai_players or len(tenpai_players) == len(PLAYERS):
        return tenpai_players
    non_tenpai = [player for player in PLAYERS if player not in tenpai_players]
    payment = 3000 // len(non_tenpai)
    receipt = 3000 // len(tenpai_players)
    for player in non_tenpai:
        scores[player] -= payment
    for player in tenpai_players:
        scores[player] += receipt
    return tenpai_players


def _context(
    state: dict,
    scores: dict[str, int],
    player: str,
    weights_by_player: dict[str, dict] | None = None,
) -> dict:
    context = {
        "rivers": state["rivers"],
        "scores": scores,
        "player": player,
        "wall_count": len(state["wall"]),
        "riichi": state["riichi"],
        "dora_indicators": state["dora_indicators"],
        "melds": [],
    }
    if weights_by_player and player in weights_by_player:
        context["weights_override"] = weights_by_player[player]
    return context


def _finish_round(
    state: dict,
    scores: dict[str, int],
    scores_before: dict[str, int],
    result: dict,
    round_number: int,
    dealer: str,
    metadata: dict,
    persist: bool,
) -> dict:
    game_log.finalize_round(state["decision_log"], result, scores_before, scores)
    record = {
        "ts": time.time(),
        "round": round_number,
        "result_type": result.get("type", "unknown"),
        "winner": result.get("winner", ""),
        "loser": result.get("loser", ""),
        "tenpai_players": result.get("tenpai_players", []),
        "riichi_pot_after": result.get("riichi_pot_after", 0),
        "scores_before": scores_before,
        "scores_after": dict(scores),
        "decisions": state["decision_log"],
        "metadata": metadata,
    }
    if persist:
        saved = game_log.save_round_log(
            round_number=round_number,
            decision_log=state["decision_log"],
            game_result=result,
            scores_before=scores_before,
            scores_after=dict(scores),
            metadata=metadata,
        )
        if saved is not None:
            record = saved
    record["dealer_continues"] = (
        result.get("winner") == dealer
        or (
            result.get("type") == "exhaustive_draw"
            and dealer in result.get("tenpai_players", [])
        )
    )
    return record


def play_round(
    *,
    seed: int,
    round_number: int,
    dealer: str,
    scores: dict[str, int],
    run_id: str,
    game_index: int,
    riichi_pot: int = 0,
    weights_by_player: dict[str, dict] | None = None,
    persist: bool = True,
) -> dict:
    """1局を最後まで進め、分析可能な牌譜レコードを返す。"""
    state = _build_round(random.Random(seed), dealer)
    scores_before = dict(scores)
    winds = _player_winds(dealer)
    metadata = {
        "source": "headless_simulation",
        "run_id": run_id,
        "game_index": game_index,
        "seed": seed,
        "weights": ai_weights.snapshot(),
    }

    while True:
        player = state["turn"]
        hand = state["hands"][player]
        if not state["has_drawn"]:
            if not state["wall"]:
                tenpai_players = _apply_tenpai_payments(scores, state["hands"])
                result = {
                    "type": "exhaustive_draw",
                    "winner": "",
                    "loser": "",
                    "tenpai_players": tenpai_players,
                    "riichi_pot_after": riichi_pot,
                }
                return _finish_round(
                    state, scores, scores_before, result, round_number,
                    dealer, metadata, persist,
                )
            hand.append(state["wall"].pop())
            state["has_drawn"] = True

        tsumo = check_tsumo(
            hand,
            is_riichi=state["riichi"][player],
            player_wind=winds[player],
            round_wind=_round_wind(round_number),
            dora_indicators=state["dora_indicators"],
            melds=None,
        )
        if tsumo:
            details = format_result(tsumo, is_tsumo=True)
            _apply_tsumo(scores, player, dealer, details)
            scores[player] += riichi_pot
            result = {
                "type": "tsumo",
                "winner": player,
                "loser": "",
                "details": details,
                "riichi_pot_after": 0,
            }
            return _finish_round(
                state, scores, scores_before, result, round_number,
                dealer, metadata, persist,
            )

        context = _context(state, scores, player, weights_by_player)
        candidates = _evaluate_hand_score(hand, context=context)
        if not candidates:
            result = {
                "type": "invalid",
                "winner": "",
                "loser": "",
                "riichi_pot_after": riichi_pot,
            }
            return _finish_round(
                state, scores, scores_before, result, round_number,
                dealer, metadata, persist,
            )

        chosen_index = len(hand) - 1 if state["riichi"][player] else candidates[0]["idx"]
        chosen = next(
            candidate for candidate in candidates if candidate["idx"] == chosen_index
        )
        if (
            not state["riichi"][player]
            and scores[player] >= 1000
            and chosen["sh"] == 0
        ):
            state["riichi"][player] = True
            scores[player] -= 1000
            riichi_pot += 1000

        state["decision_log"].append(game_log.make_cpu_decision(
            round_number=round_number,
            turn_count=state["turn_count"],
            player=player,
            hand_14=list(hand),
            chosen_idx=chosen_index,
            candidates=candidates,
            context=context,
        ))
        discarded = hand.pop(chosen_index)
        state["rivers"][player].append(discarded)

        player_index = PLAYERS.index(player)
        for offset in range(1, len(PLAYERS)):
            opponent = PLAYERS[(player_index + offset) % len(PLAYERS)]
            ron = check_ron(
                state["hands"][opponent],
                discarded,
                is_riichi=state["riichi"][opponent],
                player_wind=winds[opponent],
                round_wind=_round_wind(round_number),
                dora_indicators=state["dora_indicators"],
                melds=None,
            )
            if ron:
                details = format_result(ron, is_tsumo=False)
                points = details["main_points"]
                scores[player] -= points
                scores[opponent] += points + riichi_pot
                result = {
                    "type": "ron",
                    "winner": opponent,
                    "loser": player,
                    "details": details,
                    "riichi_pot_after": 0,
                }
                return _finish_round(
                    state, scores, scores_before, result, round_number,
                    dealer, metadata, persist,
                )

        hand.sort()
        state["turn"] = PLAYERS[(player_index + 1) % len(PLAYERS)]
        state["has_drawn"] = False
        if state["turn"] == dealer:
            state["turn_count"] += 1


def run_batch(
    games: int,
    seed: int,
    *,
    persist: bool = True,
    progress: ProgressCallback | None = None,
    weights_by_player: dict[str, dict] | None = None,
    run_id: str | None = None,
    game_index_start: int = 0,
    include_records: bool = False,
) -> dict:
    """指定半荘数を実行し、保存・分析済みのサマリーを返す。"""
    if games < 1:
        raise ValueError("games must be at least 1")
    if game_index_start < 0:
        raise ValueError("game_index_start must not be negative")

    started = time.perf_counter()
    effective_run_id = run_id or uuid.uuid4().hex[:12]
    seed_source = random.Random(seed)
    records = []
    final_scores = []

    for local_game_index in range(games):
        game_index = game_index_start + local_game_index
        scores = {player: 25000 for player in PLAYERS}
        dealer = PLAYERS[game_index % len(PLAYERS)]
        round_number = 1
        played_rounds = 0
        riichi_pot = 0
        while round_number <= 8 and played_rounds < 16:
            round_seed = seed_source.randrange(0, 2**63)
            record = play_round(
                seed=round_seed,
                round_number=round_number,
                dealer=dealer,
                scores=scores,
                run_id=effective_run_id,
                game_index=game_index,
                riichi_pot=riichi_pot,
                weights_by_player=weights_by_player,
                persist=persist,
            )
            records.append(record)
            played_rounds += 1
            riichi_pot = record.get("riichi_pot_after", 0)
            if not record["dealer_continues"]:
                dealer = PLAYERS[(PLAYERS.index(dealer) + 1) % len(PLAYERS)]
                round_number += 1
        if riichi_pot:
            leader = max(PLAYERS, key=lambda player: scores[player])
            scores[leader] += riichi_pot
        final_scores.append(dict(scores))
        if progress:
            progress(local_game_index + 1, games)

    elapsed = time.perf_counter() - started
    analysis = game_log.analyze(records)
    result_counts = Counter(record["result_type"] for record in records)
    placements = Counter()
    for scores in final_scores:
        ordered = sorted(PLAYERS, key=lambda player: scores[player], reverse=True)
        placements[ordered.index("Player") + 1] += 1

    summary = {
        "run_id": effective_run_id,
        "seed": seed,
        "games": games,
        "rounds": len(records),
        "elapsed_seconds": round(elapsed, 3),
        "rounds_per_second": round(len(records) / elapsed, 2) if elapsed else 0.0,
        "result_counts": dict(result_counts),
        "player_placements": {str(place): placements[place] for place in range(1, 5)},
        "analysis": analysis,
        "weights": ai_weights.snapshot(),
        "persisted": persist,
    }
    if include_records:
        summary["records"] = records
    return summary


def run_continuous_step(
    previous: dict | None = None,
    recent_records: list | None = None,
    *,
    seed: int | None = None,
    analysis_window_rounds: int = 200,
) -> tuple[dict, list]:
    """ランダムseedの半荘を1回追加し、連続Runの集計を更新する。"""
    if analysis_window_rounds < 1:
        raise ValueError("analysis_window_rounds must be positive")

    previous = previous or {}
    run_id = previous.get("run_id") or uuid.uuid4().hex[:12]
    game_index = int(previous.get("games", 0))
    next_seed = (
        random.SystemRandom().randrange(0, 2**53)
        if seed is None
        else int(seed)
    )
    current = run_batch(
        games=1,
        seed=next_seed,
        persist=True,
        run_id=run_id,
        game_index_start=game_index,
        include_records=True,
    )

    result_counts = Counter(previous.get("result_counts", {}))
    result_counts.update(current["result_counts"])
    placements = Counter(previous.get("player_placements", {}))
    placements.update(current["player_placements"])
    games = game_index + 1
    rounds = int(previous.get("rounds", 0)) + current["rounds"]
    elapsed = float(previous.get("elapsed_seconds", 0)) + current["elapsed_seconds"]
    records = [
        *(recent_records or []),
        *current.pop("records"),
    ][-analysis_window_rounds:]
    seed_history = [*previous.get("seed_history", []), next_seed][-100:]

    summary = {
        "run_id": run_id,
        "seed": next_seed,
        "latest_seed": next_seed,
        "seed_history": seed_history,
        "games": games,
        "rounds": rounds,
        "elapsed_seconds": round(elapsed, 3),
        "rounds_per_second": round(rounds / elapsed, 2) if elapsed else 0.0,
        "result_counts": dict(result_counts),
        "player_placements": {
            str(place): placements[str(place)] for place in range(1, 5)
        },
        "analysis": game_log.analyze(records),
        "analysis_window_rounds": len(records),
        "weights": ai_weights.snapshot(),
        "persisted": True,
        "continuous": True,
    }
    return summary, records


def run_seed_suite(
    seeds: list[int],
    *,
    weights_by_player: dict[str, dict] | None = None,
) -> dict:
    """独立した局seed群を実行し、Player視点の比較指標を返す。"""
    if not seeds:
        raise ValueError("seeds must not be empty")
    records = []
    score_deltas = []
    placements = []
    for index, seed in enumerate(seeds):
        scores = {player: 25000 for player in PLAYERS}
        record = play_round(
            seed=seed,
            round_number=(index % 8) + 1,
            dealer=PLAYERS[index % len(PLAYERS)],
            scores=scores,
            run_id="paired-evaluation",
            game_index=index,
            weights_by_player=weights_by_player,
            persist=False,
        )
        pot = record.get("riichi_pot_after", 0)
        if pot:
            leader = max(PLAYERS, key=lambda player: scores[player])
            scores[leader] += pot
        records.append(record)
        score_deltas.append(scores["Player"] - 25000)
        placements.append(
            1 + sum(score > scores["Player"] for score in scores.values())
        )

    analysis = game_log.analyze(records, focus_player="Player")
    return {
        "rounds": len(records),
        "average_score_delta": round(sum(score_deltas) / len(score_deltas), 1),
        "average_placement": round(sum(placements) / len(placements), 2),
        "placements": placements,
        "score_deltas": score_deltas,
        "analysis": analysis,
    }
