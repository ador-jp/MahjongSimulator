import unittest

from game_logic import find_best_discard, get_discard_candidates


class DiscardEvaluationTests(unittest.TestCase):
    def test_attack_prefers_more_live_acceptance_at_same_shanten(self):
        hand = [
            "5p", "3p", "2z", "7m", "8p", "8p", "5s",
            "8m", "7p", "4z", "6s", "5z", "9m", "5z",
        ]
        context = {
            "player": "Player",
            "rivers": {},
            "riichi": {},
            "wall_count": 60,
            "scores": {
                "Player": 25000,
                "CPU1": 25000,
                "CPU2": 25000,
                "CPU3": 25000,
            },
        }

        candidates = get_discard_candidates(hand, context=context, top_k=len(hand))
        minimum_shanten = min(candidate["sh"] for candidate in candidates)
        best_acceptance = max(
            candidate["weighted_acc"]
            for candidate in candidates
            if candidate["sh"] == minimum_shanten
        )
        selected_idx, selected_shanten = find_best_discard(hand, context=context)
        selected = next(candidate for candidate in candidates if candidate["idx"] == selected_idx)

        self.assertEqual(minimum_shanten, selected_shanten)
        self.assertEqual(best_acceptance, selected["weighted_acc"])

    def test_defense_uses_riichi_players_genbutsu(self):
        hand = [
            "1m", "5m", "2m", "8m", "2p", "5p", "8p",
            "2s", "5s", "8s", "1z", "2z", "3z", "4z",
        ]
        context = {
            "player": "Player",
            "rivers": {
                "Player": [],
                "CPU1": ["5m"],
                "CPU2": ["1m"],
                "CPU3": [],
            },
            "riichi": {"CPU1": True, "CPU2": False, "CPU3": False},
            "wall_count": 30,
            "scores": {
                "Player": 25000,
                "CPU1": 25000,
                "CPU2": 25000,
                "CPU3": 25000,
            },
        }

        selected_idx, _ = find_best_discard(hand, context=context)

        self.assertEqual("5m", hand[selected_idx])


if __name__ == "__main__":
    unittest.main()
