import unittest

from game_logic import evaluate_discard, find_best_discard, get_discard_candidates


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

    def test_top_candidates_list_each_tile_once(self):
        hand = [
            "2m", "1s", "8m", "5s", "2m", "7p", "6p",
            "6s", "7p", "6m", "2z", "4p", "1m", "8p",
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

        raw_top_tiles = [
            candidate["tile"]
            for candidate in get_discard_candidates(hand, context=context, top_k=3)
        ]
        self.assertLess(len(set(raw_top_tiles)), 3)

        result = evaluate_discard(hand, 0, context=context)
        top_tiles = [candidate["tile"] for candidate in result["top_candidates"]]

        self.assertEqual(3, len(top_tiles))
        self.assertEqual(len(top_tiles), len(set(top_tiles)))
        self.assertEqual(result["best_tile"], top_tiles[0])


if __name__ == "__main__":
    unittest.main()
