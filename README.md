# MahjongSimulator

**A self-learning riichi mahjong AI — built to become a coach that teaches beginners *why*, not just *what*.**

Riichi mahjong is famously hard to learn by feel. There is no single "right" discard you can memorize: the best move changes with your hand, the discards on the table, the score situation, and who has declared riichi. Strong mahjong AI already exists, but it only hands you the answer. This project has a different goal:

> An AI that learns from millions of self-play games, always picks the optimal discard — and explains the theory behind it in plain language, so a beginner who studies with it actually gets better.

This repository is the engine room of that coach: a full mahjong engine, a headless self-play simulator, a decision-analysis pipeline that extracts *good* and *bad* decisions with concrete reasons, and a conservative learning loop that only adopts AI tuning after it survives validation on unseen data.

日本語の説明は下部にあります。

## What exists today

- **Full 4-player riichi mahjong engine** (`game_logic.py`) — shanten calculation, tile efficiency (ukeire), dora, safety evaluation against each opponent, and situational corrections. Handles pon / chi / kan, riichi, tsumo and ron.
- **Playable app + learning dashboard** (`app.py`, Streamlit) — a game UI with tile images, plus a high-speed AI learning dashboard: game speed, games played, late-game tenpai rate, deal-in rate, result and placement graphs, diagnostics, tuning proposals, and JSON export.
- **Headless self-play simulation** (`simulation.py`) — reproducible fixed-seed games, plus continuous runs that keep adding random-seed hanchan while switched on. Rendering and sleeps are kept out of the fast path.
- **Decision logging** (`game_log.py`) — every game is recorded as JSONL with its run id, seed, the exact AI weights used, hands, discards, scores, dora indicators, the situation category, the chosen tile, and the top alternative candidates. Logs are read in a streaming fashion, never loaded whole into memory.
- **Situation classification** (`decision_context.py`) — each decision is categorized by game phase (early / middle / late) × riichi pressure (none / one / multiple) × distance to tenpai × score position (leading / close / trailing), so learning and explanations can be per-situation instead of one-size-fits-all.
- **Learning with adoption gates** (`learning.py`) — extracts good and bad decisions per situation and proposes conservative weight changes. A candidate is adopted only if it beats the current AI on the *same* seeds (A/B) **and** on a separate set of *unseen* seeds, improving both average score and average placement without worsening the deal-in rate or late-game tenpai rate. Even then, weights are never applied automatically: a human approves, and every change is written to an audit trail (`ai_tuning_history.jsonl`) with before / after values, the diff, and the metrics that justified it.
- **A real dataset** — about **3.9 GB of self-play game logs** have been accumulated locally. They are not in this repository (GitHub file-size limits); the code regenerates them.

## Why the explanations are the point

The analysis pipeline already produces explanations in the form a teacher needs: for a given decision it can show the concrete hand, the discards on the table, the tile that was chosen, the alternative that should have been chosen, and the reason — grouped by the situation the player was in. Turning those analyzed decisions into beginner-friendly lessons is the product this simulator exists to feed.

## Quick start

Requirements: Python 3.11+ (developed on 3.13).

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Run the tests:

```sh
python -m unittest -v test_game_logic.py test_simulation.py
```

## Project layout

| File | Role |
| --- | --- |
| `game_logic.py` | Mahjong rules and discard evaluation (shanten, efficiency, safety, situational corrections) |
| `simulation.py` | Fixed-seed and continuous headless self-play |
| `game_log.py` | JSONL game records, aggregation, diagnostics, tuning proposals, audit history |
| `decision_context.py` | Classifies a decision into a learnable situation category |
| `learning.py` | Good/bad decision extraction, per-situation weight candidates, A/B + unseen-seed validation gates |
| `ai_weights.py` | AI evaluation weights and JSON persistence (`ai_weights.json`) |
| `app.py` | Streamlit game UI and AI learning dashboard |
| `analyze_logs.py` | Standalone log analysis |
| `test_game_logic.py` / `test_simulation.py` | Regression tests (discard evaluation; recording, score conservation, analysis/adoption flow) |
| `images/` | Tile images used by the UI |
| `tile_hover_component/` | Streamlit component for tile hover details |

## Design principles

- **Reproducibility first** — the same seed and the same weights always replay the same game.
- **Score conservation** — at the end of every hand, the four scores plus uncollected riichi sticks total exactly 100,000 points. The tests enforce it.
- **No unmeasured "improvements"** — a tuning change is never claimed to help until it passes both the same-seed A/B comparison and the unseen-seed validation.
- **No silent auto-tuning** — the AI never rewrites its own weights unconditionally; adoption requires an explicit human action and leaves an audit record.
- **Stable log format** — the JSONL record format and public functions are kept backward compatible so old logs stay analyzable.

## Roadmap

- Turn the decision analysis into a beginner-facing explanation/lesson engine (the coach itself).
- Publish the coach as a web app: practice "what to discard?" situations with theory, generated from real self-play decisions.
- Scale self-play and per-situation tuning, with the same validation gates, and publish the results.

## 日本語

麻雀は「感覚」で覚えるのが難しいゲームです。手牌、河、点数状況、リーチの有無で最適な打牌が変わるため、丸暗記では上達しません。強い麻雀AIはすでに存在しますが、返してくれるのは「答え」だけです。このプロジェクトの目的は違います。

**自己対局で学習し、常に最適な打牌を選び、その理由を理論として分かりやすく解説してくれるAI。** それを教材に学べば、初心者でも上手くなれる——その「先生」を作るための土台が、このリポジトリです。

現在の構成:

- 4人打ちリーチ麻雀の完全な対局エンジン（シャンテン、受け入れ、ドラ、安全度、状況補正、鳴き・リーチ・ツモ・ロン対応）
- Streamlit製の対局画面と、高速AI学習ダッシュボード（速度、局数、終盤聴牌率、放銃局率、結果・順位グラフ、診断、調整案）
- seed固定で再現可能な対局と、ランダムseedで継続するヘッドレス自己対局シミュレーション
- 判断の記録:手牌・河・点数・ドラ・局面カテゴリ・選択牌・上位候補をJSONLに保存（ローカルには約3.9GBの対局ログが蓄積。容量の都合でリポジトリには含みません）
- 局面分類:序盤/中盤/終盤 × リーチ圧 × テンパイまでの距離 × 点数位置
- 検証ゲート付き学習:局面別の好手・悪手を抽出し、同一seedでのA/B比較と未使用seedでの検証の両方に合格した重み調整だけを候補化。適用は人間の明示操作が必要で、変更履歴はすべて監査ログに残ります

起動方法とテストは上部の Quick start を参照してください。

## Status

Personal research project, under active development. The simulator, analysis, and gated learning loop work end to end; the beginner-facing coach UI is the next step on the roadmap.
