import json
import copy
import tempfile
import unittest
from pathlib import Path

import ai_weights
import game_log
import learning
import simulation


class SimulationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.original_log_file = game_log.LOG_FILE
        self.original_history_file = game_log.TUNING_HISTORY_FILE
        self.original_weights_file = ai_weights._WEIGHTS_FILE
        self.original_weights = json.loads(json.dumps(ai_weights.get()))
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        game_log.LOG_FILE = root / "game_logs.jsonl"
        game_log.TUNING_HISTORY_FILE = root / "ai_tuning_history.jsonl"
        ai_weights._WEIGHTS_FILE = root / "ai_weights.json"

    def tearDown(self):
        game_log.LOG_FILE = self.original_log_file
        game_log.TUNING_HISTORY_FILE = self.original_history_file
        ai_weights.reset_to_default()
        ai_weights.update(self.original_weights)
        ai_weights._WEIGHTS_FILE = self.original_weights_file
        self.tempdir.cleanup()

    def test_round_is_persisted_with_replay_metadata_and_conserved_points(self):
        scores = {player: 25000 for player in simulation.PLAYERS}

        record = simulation.play_round(
            seed=20260915,
            round_number=1,
            dealer="Player",
            scores=scores,
            run_id="test-run",
            game_index=0,
            persist=True,
        )

        saved = game_log.load_logs(max_rounds=1)[0]
        self.assertEqual("test-run", saved["metadata"]["run_id"])
        self.assertEqual(20260915, saved["metadata"]["seed"])
        self.assertTrue(saved["decisions"])
        self.assertTrue(all(decision["policy"] == "ai" for decision in saved["decisions"]))
        first_decision = saved["decisions"][0]
        self.assertTrue(first_decision["context_key"])
        self.assertEqual(14, len(first_decision["hand"]))
        self.assertTrue(first_decision["alternatives"])
        self.assertIn("chosen", first_decision)
        self.assertEqual(1, len(game_log.load_logs_for_run("test-run")))
        self.assertEqual(100000, sum(scores.values()) + record["riichi_pot_after"])

    def test_analysis_proposes_and_persists_conservative_adjustment(self):
        rounds = []
        for index in range(20):
            rounds.append({
                "result_type": "exhaustive_draw",
                "winner": "",
                "loser": "",
                "scores_before": {"Player": 25000},
                "scores_after": {"Player": 25000},
                "decisions": [{
                    "policy": "ai",
                    "player": "Player",
                    "wall_count": 20,
                    "riichi_count": 0,
                    "sh_after": 2,
                    "regret": 0,
                    "is_strictly_best": True,
                    "safety_raw": 50,
                    "outcome": "draw",
                }],
            })

        analysis = game_log.analyze(rounds)
        differences, notes = game_log.suggest_tuning(analysis)
        record = game_log.apply_tuning(
            differences,
            analysis=analysis,
            source="test-run",
        )

        self.assertEqual(100.0, analysis["draw_rate"])
        self.assertEqual(0.0, analysis["late_tenpai_rate"])
        self.assertLess(differences["acc_norm"], self.original_weights["acc_norm"])
        self.assertTrue(notes)
        self.assertEqual("test-run", record["source"])
        self.assertTrue(ai_weights._WEIGHTS_FILE.exists())
        self.assertEqual(1, len(game_log.load_tuning_history()))

    def test_context_examples_and_profiles_are_specific(self):
        context = {
            "key": "late:no_riichi:two_plus:close",
            "phase": "late",
            "pressure": "no_riichi",
            "distance": "two_plus",
            "position": "close",
        }
        rounds = []
        for index in range(20):
            decision = {
                "policy": "ai",
                "player": "Player",
                "round": 8,
                "turn": index,
                "wall_count": 15,
                "riichi_count": 0,
                "context_key": context["key"],
                "context": context,
                "hand": ["1m"] * 14,
                "rivers": {},
                "scores": {"Player": 25000},
                "dora_indicators": ["1z"],
                "chosen_tile": "1m",
                "best_tile": "1m",
                "chosen": {
                    "idx": 0,
                    "tile": "1m",
                    "sh": 2,
                    "weighted_acc": 8,
                    "safety_raw": 35,
                },
                "alternatives": [{
                    "idx": 0,
                    "tile": "1m",
                    "sh": 2,
                    "weighted_acc": 8,
                    "safety_raw": 35,
                }],
                "sh_after": 2,
                "regret": 0,
                "is_strictly_best": True,
                "safety_raw": 35,
                "outcome": "draw",
            }
            rounds.append({"decisions": [decision]})

        examples = learning.extract_decision_examples(rounds)
        proposal = learning.propose_context_weights(rounds)

        self.assertTrue(examples["good"])
        self.assertEqual(1, len(proposal["changes"]))
        self.assertEqual(context["key"], proposal["changes"][0]["context_key"])
        self.assertEqual(
            23.0,
            proposal["candidate_weights"]["context_profiles"][context["key"]]["acc_norm"],
        )

    def test_paired_evaluation_uses_disjoint_seeds_and_adoption_gate(self):
        baseline = ai_weights.snapshot()
        candidate = copy.deepcopy(baseline)
        context_key = "early:no_riichi:two_plus:close"
        candidate["context_profiles"] = {
            context_key: {"acc_norm": 20.0}
        }
        proposal = {
            "baseline_weights": baseline,
            "candidate_weights": candidate,
            "changes": [{
                "context_key": context_key,
                "label": "序盤 / リーチなし / 2シャンテン以上 / 接戦",
                "profile": {"acc_norm": 20.0},
                "reasons": ["test"],
                "sample_size": 20,
            }],
        }

        evaluation = learning.evaluate_context_candidate(
            proposal,
            seed=12345,
            training_rounds=1,
            validation_rounds=1,
        )

        self.assertEqual(
            evaluation["training"]["baseline"]["score_deltas"],
            simulation.run_seed_suite(
                evaluation["training_seeds"],
                weights_by_player={
                    player: baseline for player in simulation.PLAYERS
                },
            )["score_deltas"],
        )
        for split in ("training", "validation"):
            comparison = evaluation[split]
            self.assertIn("placement_gain", comparison)
            self.assertEqual(
                comparison["score_gain"] > 0
                and comparison["placement_gain"] > 0
                and comparison["deal_in_rate_change"] <= 0
                and comparison["late_tenpai_rate_change"] >= 0,
                evaluation[f"{split}_passed"],
            )
        self.assertTrue(
            set(evaluation["training_seeds"]).isdisjoint(
                evaluation["validation_seeds"]
            )
        )
        self.assertIsNone(
            learning.apply_validated_candidate({
                **evaluation,
                "accepted": False,
            })
        )
        self.assertIsNone(
            learning.apply_validated_candidate({
                **evaluation,
                "accepted": True,
                "training_passed": True,
                "validation_passed": False,
            })
        )

        accepted = {
            **evaluation,
            "accepted": True,
            "training_passed": True,
            "validation_passed": True,
            "baseline_weights": ai_weights.snapshot(),
            "candidate_weights": candidate,
        }
        record = learning.apply_validated_candidate(accepted)
        self.assertIsNotNone(record)
        self.assertEqual(
            {"acc_norm": 20.0},
            ai_weights.get()["context_profiles"][context_key],
        )


if __name__ == "__main__":
    unittest.main()
