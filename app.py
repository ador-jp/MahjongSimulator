import streamlit as st
import random
import time
import base64
from pathlib import Path

from st_clickable_images import clickable_images


# ==========================================
# 0. 画像パスとBase64変換 (赤ドラ対応)
# ==========================================
def get_tile_b64(tile_id, style="0"):
    is_red = tile_id.endswith("r")
    base_id = tile_id.replace("r", "")
    num, suit = base_id[0], base_id[1]
    s = {"m": "m", "p": "p", "s": "s", "z": "j"}.get(suit, suit)

    # 教えていただいた赤ドラ例外規則
    if is_red:
        red_map = {
            "3m": "c",
            "5m": "e",
            "3p": "c",
            "5p": "e",
            "1s": "a",
            "3s": "c",
            "5s": "e",
            "7s": "g",
        }
        num = red_map.get(base_id, num)

    filename = f"{style}{s}{num}.png"
    # プロジェクトルートのimagesフォルダを絶対パスで参照
    img_path = Path(__file__).parent.absolute() / "images" / filename

    if img_path.exists():
        with open(img_path, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    return ""


# ==========================================
# 1. 状態管理
# ==========================================
# ==========================================
# 0.5 Helper Functions
# ==========================================
def tile_sort_key(tile):
    """
    理牌の並び順定義:
    1. スート: 萬子(m) -> 筒子(p) -> 索子(s) -> 字牌(z)
    2. 数字: 1-9
    3. 赤ドラ: 赤(r付き)は通常牌扱い（あるいは隣接）
    """
    base = tile.replace("r", "")
    num_str = base[0]
    suit = base[1]

    # スートの優先順位
    suit_order = {"m": 0, "p": 1, "s": 2, "z": 3}
    s_val = suit_order.get(suit, 99)

    # 数字
    try:
        n_val = int(num_str)
    except ValueError:
        n_val = 0

    # 赤ドラの扱い（同じ牌なら赤を後ろにするなど）
    is_red = 1 if "r" in tile else 0

    return (s_val, n_val, is_red)


# ==========================================
# 1. 状態管理
# ==========================================
if "gs" not in st.session_state:
    # 山札と配牌の初期化
    wall = [f"{i}{s}" for s in "mps" for i in range(1, 10) for _ in range(4)] + [
        f"{i}z" for i in range(1, 8) for _ in range(4)
    ]
    random.shuffle(wall)

    # ドラ表示牌（山札の適当な位置、ここでは王牌として末尾から確保）
    dora_indicator = wall[-10]

    p_names = ["Player", "CPU1", "CPU2", "CPU3"]
    hands = {
        p: sorted([wall.pop() for _ in range(13)], key=tile_sort_key) for p in p_names
    }
    # プレイヤーは親として1枚余分に引く（仮）- 実際は親決めロジックが必要だが既存踏襲で14枚スタート
    hands["Player"].append(wall.pop())

    st.session_state.gs = {
        "wall": wall,
        "hands": hands,
        "rivers": {p: [] for p in p_names},
        "turn": "Player",
        "has_drawn": True,  # 最初は配牌で14枚ある前提
        "turn_count": 0,
        "dora_indicator": dora_indicator,
    }

GS = st.session_state.gs


def handle_discard(idx):
    """手札が14枚の時だけ打牌を許可"""
    if len(GS["hands"]["Player"]) >= 14:
        # 打牌処理
        tile = GS["hands"]["Player"].pop(idx)
        GS["rivers"]["Player"].append(tile)
        GS["hands"]["Player"].sort(key=tile_sort_key)  # 理牌

        GS["turn"] = "CPU1"
        GS["has_drawn"] = False
        GS["turn_count"] += 1
        st.rerun()


# ==========================================
# 2. UI 表示
# ==========================================
st.set_page_config(layout="wide")

# サイドバー設定
st.sidebar.title("メニュー")
auto_play = st.sidebar.checkbox("高速オートプレイ", value=False)
st.sidebar.button("🔄 リセット", on_click=lambda: st.session_state.clear())

# 卓の状況 (河)
st.write(f"### 対局ボード (残り山札: {len(GS['wall'])})")
st.image(get_tile_b64(GS["dora_indicator"], "1"), width=40, caption="ドラ表示牌")

# --- 河 (River) 表示 ---
cols = st.columns(4)
for i, p in enumerate(["Player", "CPU1", "CPU2", "CPU3"]):
    with cols[i]:
        st.caption(f"**{p}**")
        river_html = "".join(
            [
                f'<img src="{get_tile_b64(t, "1")}" style="height:35px; margin:1px;">'
                for t in GS["rivers"][p]
            ]
        )
        # 固定高さ(height: 160px)を確保して、捨て牌が増えてもUIがガタつかないようにする
        st.markdown(
            f'<div style="background:#1e272e; padding:5px; border-radius:5px; height:160px; overflow-y:auto;">{river_html}</div>',
            unsafe_allow_html=True,
        )

st.divider()

# --- 手牌表示 (常時表示) ---
st.write("### あなたの手牌")

# ツモがある場合（14枚）、14枚目を少し離して表示したいが、clickable_imagesは一括。
# 視覚的な区切りのために、リストの並びは [sorted_13] + [drawn_tile] になっていることを確認。
# プレイヤーの手牌取得
player_hand_imgs = [get_tile_b64(t, "0") for t in GS["hands"]["Player"]]

# 手牌のクリック判定 (プレイヤーのターンのみ有効)
# 常に表示するが、アクションはターンチェックで行う
with st.container():
    # ターンによってキーを変えるか？ -> 変えないと選択状態が残るかも
    # 13枚の時と14枚の時で表示が変わる。
    clicked = clickable_images(
        player_hand_imgs,
        div_style={
            "display": "flex",
            "gap": "2px",
            "justify-content": "center",
            "padding": "10px",
            "flex-wrap": "nowrap",
            "min-height": "40px",  # ちらつき防止
        },
        img_style={
            "height": "60px",
            "cursor": "pointer" if GS["turn"] == "Player" else "default",
            "border-radius": "3px",
            "transition": "transform 0.1s",
        },
        # ツモ牌（最後）だけマージンを変えるのはCSSセレクタがないと厳しい。
        # 代替案: img_styleは全画像適用。
        # ここではシンプルに表示。
        key=f"hand_view_{GS['turn_count']}_{len(GS['hands']['Player'])}",
    )

# プレイヤーのアクション処理
if GS["turn"] == "Player":
    # まだツモっていないならツモる
    if not GS["has_drawn"]:
        # 山がない場合...は一旦考慮せずエラー回避
        if GS["wall"]:
            new_tile = GS["wall"].pop()
            GS["hands"]["Player"].append(
                new_tile
            )  # 末尾に追加（理牌しないことでツモ牌がわかる）
            GS["has_drawn"] = True
            st.rerun()
        else:
            st.warning("流局")
            st.stop()

    # オートプレイ（ツモ切り）
    if auto_play:
        # 少し待つ（視認性のため、高速なら0.1s以下でもOK）
        time.sleep(0.1)
        # ツモ切り（最後の牌を捨てる）
        handle_discard(len(GS["hands"]["Player"]) - 1)

    # ツモり済み、打牌待ち
    if clicked > -1:
        handle_discard(clicked)

# CPU 進行
else:
    # プレイヤー以外の手番
    # オートプレイ時はCPU思考時間も短縮するか？ -> "高速"なら短縮したい
    delay = 0.05 if auto_play else 0.4
    time.sleep(delay)
    cp = GS["turn"]

    if not GS["has_drawn"]:
        if GS["wall"]:
            GS["hands"][cp].append(GS["wall"].pop())
            GS["has_drawn"] = True
            st.rerun()
        else:
            st.warning("流局")
            st.stop()
    else:
        # CPUはツモ切り(13番目のインデックス=14枚目を捨てる)
        # あるいはランダム？ ここではツモ切り
        discard_idx = len(GS["hands"][cp]) - 1
        tile = GS["hands"][cp].pop(discard_idx)
        GS["rivers"][cp].append(tile)

        # 次のプレイヤーへ
        order = ["Player", "CPU1", "CPU2", "CPU3"]
        current_idx = order.index(cp)
        next_player = order[(current_idx + 1) % 4]

        GS["turn"] = next_player
        GS["has_drawn"] = False

        # プレイヤーに順番が回る時はカウントアップ（キー更新等のため）
        if next_player == "Player":
            GS["turn_count"] += 1

        st.rerun()
