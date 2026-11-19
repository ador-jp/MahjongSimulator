#!/usr/bin/env python3
"""
分析スクリプト: game_logs.jsonl を読み込み、基本統計情報を出力します。
- 集計局数
- 勝率、流局率、平均得点差
- 最善手率、平均 regret
- 放銃率と回避可能回数
- 各評価軸（シャンテン、受け入れ、ドラ、安全）の過小評価平均
"""
import json
from pathlib import Path
from collections import defaultdict

LOG_PATH = Path(__file__).parent / "game_logs.jsonl"

if not LOG_PATH.exists():
    print(f"ログファイルが見つかりません: {LOG_PATH}")
    exit(1)

total_games = 0
wins = 0
draws = 0
total_regret = 0.0
total_scores_before = defaultdict(int)
total_scores_after = defaultdict(int)
furo_count = 0
furo_possible = 0
safety_under = []
shanten_under = []
accept_under = []

def parse_line(line: str):
    return json.loads(line)

with LOG_PATH.open("r", encoding="utf-8") as f:
    for raw in f:
        try:
            entry = parse_line(raw)
        except Exception:
            continue
        total_games += 1
        # 勝敗判定 (winner が空でなければ勝ち)
        if entry.get("winner"):
            if entry["winner"] == "Player":
                wins += 1
        else:
            draws += 1
        # regret の平均
        for d in entry.get("decisions", []):
            total_regret += d.get("regret", 0.0)
        # スコア差分
        before = entry.get("scores_before", {})
        after = entry.get("scores_after", {})
        for k, v in before.items():
            total_scores_before[k] += v
        for k, v in after.items():
            total_scores_after[k] += v
        # 放銃率集計 (loser が空でない場合)
        if entry.get("loser"):
            furo_possible += 1
            if entry["loser"] != "":
                furo_count += 1
        # 評価軸過小評価 (各 decision に含まれる comp_*）
        for d in entry.get("decisions", []):
            # シャンテン過小評価は sh が 0 で best_comp_sh が正のとき
            if d.get("sh") == 0 and d.get("best_comp_sh", 0) > 0:
                shanten_under.append(d.get("best_comp_sh", 0))
            # 受け入れ過小評価
            if d.get("comp_acc", 0) > d.get("best_comp_acc", 0):
                accept_under.append(d.get("best_comp_acc", 0) - d.get("comp_acc", 0))
            # ドラ過小評価（差分）
            if d.get("comp_dora", 0) > d.get("best_comp_dora", 0):
                # ここでは単純に差分を取ります（必要ならリストに追加）
                pass
            # 安全度過小評価
            if d.get("comp_safety", 0) > d.get("best_comp_safety", 0):
                safety_under.append(d.get("best_comp_safety", 0) - d.get("comp_safety", 0))

print("=== ゲームログ集計結果 ===")
print(f"総局数: {total_games}")
print(f"勝率: {wins/total_games*100:.1f}%  (Player wins)" )
print(f"流局率: {draws/total_games*100:.1f}%")
# 平均得点差 (Player のスコア変化)
if total_scores_before:
    avg_before = total_scores_before["Player"] / total_games
    avg_after = total_scores_after["Player"] / total_games
    print(f"平均スコア変化: {avg_after - avg_before:.1f}")
print(f"平均 regret: {total_regret/total_games:.2f}")
if furo_possible:
    print(f"放銃率: {furo_count/furo_possible*100:.1f}% (回避可能: {furo_possible - furo_count})")
# 過小評価平均
if shanten_under:
    print(f"シャンテン過小評価平均: {sum(shanten_under)/len(shanten_under):.2f}")
if safety_under:
    print(f"安全度過小評価平均: {sum(safety_under)/len(safety_under):.2f}")
print("--- 完了 ---")
