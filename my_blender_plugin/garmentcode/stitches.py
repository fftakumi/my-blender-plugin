"""ステッチ情報から縫合エッジ(面を持たない loose edge)を張る(bpy 非依存)。

Blender の Cloth は「面のない孤立エッジ」を縫合スプリングとして扱う。
なので取り込み時にやるのは、縫い合わせる2本の境界エッジの頂点を
**正規化弧長で対応付けて孤立エッジにする** ことだけ。

対応付けで踏む罠が2つあり、どちらも参照実装が実測で潰してある:

- **向き。** spec のエッジ向きどおりに素直に繋ぐと、相手が逆向きのときに
  筒がねじれる。順・逆の両方でマッチを取り、**総距離が短い方**を採る
  (参照データでは 40 本中 40 本で B 側の反転が必要だった)
- **ダーツ先端の自己ループ。** 2本のエッジが同じ頂点を共有するので i == j の
  ペアができる。無意味なので捨てる(参照データで 8 件、いずれも無害)
"""

import math


def normalized_params(chain, verts):
    """頂点列の正規化弧長パラメータ [0..1] を返す純粋関数"""
    lengths = [0.0]
    for index in range(1, len(chain)):
        a = verts[chain[index]]
        b = verts[chain[index - 1]]
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total < 1e-9:
        return [0.0] * len(chain)
    return [value / total for value in lengths]


def match_chains(chain_a, params_a, chain_b, params_b):
    """両方向の最近傍マッチの和集合を返す純粋関数

    片方向だけだと、点数の少ない側の頂点にスプリングが1本も付かないことがある。
    どちらの頂点も必ず1本は持つように和を取る。
    """
    pairs = set()
    for index, param in enumerate(params_a):
        nearest = min(range(len(params_b)), key=lambda k: abs(params_b[k] - param))
        pairs.add((chain_a[index], chain_b[nearest]))
    for index, param in enumerate(params_b):
        nearest = min(range(len(params_a)), key=lambda k: abs(params_a[k] - param))
        pairs.add((chain_a[nearest], chain_b[index]))
    return pairs


def pair_stitch(chain_a, chain_b, verts):
    """1組のステッチについて (ペア集合, 反転したか) を返す純粋関数

    順・逆の両方でマッチを取り、頂点間距離の総和が小さい方を採る。
    """
    params_a = normalized_params(chain_a, verts)
    params_b = normalized_params(chain_b, verts)
    reversed_chain = list(reversed(chain_b))
    reversed_params = [1.0 - value for value in reversed(params_b)]

    forward = match_chains(chain_a, params_a, chain_b, params_b)
    backward = match_chains(chain_a, params_a, reversed_chain, reversed_params)
    cost_forward = sum(math.dist(verts[i], verts[j]) for i, j in forward)
    cost_backward = sum(math.dist(verts[i], verts[j]) for i, j in backward)
    if cost_backward < cost_forward:
        return backward, True
    return forward, False


def build_sewing_edges(stitches, chains_by_panel, verts):
    """(縫合エッジのソート済みリスト, 統計) を返す純粋関数

    chains_by_panel は {(パネル名, エッジ添字): [全体の頂点添字, ...]}。
    エッジが見つからないステッチは飛ばして統計の "missing" に数える。
    """
    sewing = set()
    self_loops = 0
    reversed_count = 0
    missing = []
    matched = 0
    for side_a, side_b in stitches:
        key_a = (side_a["panel"], side_a["edge"])
        key_b = (side_b["panel"], side_b["edge"])
        chain_a = chains_by_panel.get(key_a)
        chain_b = chains_by_panel.get(key_b)
        if not chain_a or not chain_b:
            # 両側とも欠けていることがある。片方だけ報告すると、直して再取り込みしても
            # もう片方がまた出てくる
            if not chain_a:
                missing.append(key_a)
            if not chain_b:
                missing.append(key_b)
            continue
        pairs, was_reversed = pair_stitch(chain_a, chain_b, verts)
        reversed_count += 1 if was_reversed else 0
        matched += 1
        for i, j in pairs:
            if i == j:
                # ダーツの先端では2本のエッジが同じ頂点を共有する。自己ループは無意味
                self_loops += 1
                continue
            sewing.add((i, j) if i < j else (j, i))
    stats = {
        "stitches": matched,
        "self_loops": self_loops,
        "reversed": reversed_count,
        "missing": missing,
    }
    return sorted(sewing), stats


def gap_stats(sewing_edges, verts):
    """縫合エッジの初期ギャップ(平均・最大)を返す純粋関数。空なら None"""
    if not sewing_edges:
        return None
    gaps = [math.dist(verts[i], verts[j]) for i, j in sewing_edges]
    return {"mean": sum(gaps) / len(gaps), "max": max(gaps), "count": len(gaps)}
