"""連結成分まわりの小道具(bpy 非依存)。

面の連結成分数はパネルが意図せず溶接されていないかの検算に使う
(パネル数と一致しなければ、どこかで全体マージが掛かっている)。
"""


class UnionFind:
    """経路圧縮つき Union-Find。要素は任意のハッシュ可能な値"""

    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def count_face_components(vertex_count, faces):
    """面だけを辿った連結成分の数を返す純粋関数(縫合エッジは無視する)"""
    union = UnionFind(range(vertex_count))
    for face in faces:
        for index in range(1, len(face)):
            union.union(face[0], face[index])
    return len({union.find(face[0]) for face in faces})


def weld_blockers(faces):
    """面から「同じ溶接グループに入れてはいけない頂点」の対応表を返す純粋関数

    面の辺で 1〜2 ホップ以内の頂点同士を禁じる。理由は2つあり、どちらも実測:

    - 1ホップ: 三角形の辺の両端が1点になると、その三角形が潰れる
    - 2ホップ: 辺を共有する2枚の三角形の外側2頂点が1点になると、2枚が同じ
      3頂点に折り重なって片方が消える

    別パネル同士は布の辺で繋がっていないので、2ホップまで禁じても縫合は通る。
    """
    one_hop = {}
    for face in faces:
        count = len(face)
        for index in range(count):
            a, b = face[index], face[(index + 1) % count]
            one_hop.setdefault(a, set()).add(b)
            one_hop.setdefault(b, set()).add(a)
    blockers = {}
    for vertex, neighbours in one_hop.items():
        reach = set(neighbours)
        for neighbour in neighbours:
            reach |= one_hop.get(neighbour, ())
        reach.discard(vertex)
        blockers[vertex] = reach
    return blockers


def pair_groups(pairs, blocked=None):
    """頂点ペアの列を連結成分ごとにまとめて ([[頂点, ...], ...], 見送ったペア数) を返す純粋関数

    溶接は「全体 merge by distance」ではなく **このグループ単位** で行う。
    距離ではなくペアの繋がりで決めるので、たまたま近いだけの別パネルは溶接されない。
    出力は決定的(各グループは昇順、グループ同士は先頭の昇順)。

    ★ blocked を渡すこと。{頂点: {同じグループに入れてはいけない頂点, ...}} を渡すと、
      **同じ布の近い頂点同士が1グループに入るのを拒否** する
      (呼ぶ側が面エッジの2ホップ近傍を渡す。理由は weld.py)。

      これが無いと本物の面が消える。縫合のマッチは両方向の最近傍の和なので、
      両辺の点数が違うと(袖 21 点 ↔ カフ 8 点のように、リサンプルが独立な以上
      これが普通)複数の袖頂点が同じカフ頂点に当たり、その袖頂点同士が
      **推移的に同じグループへ入る**。そのまま weld すると間の三角形が潰れる
      (実測: 90 グループ中 36 が該当し、面が 2458 -> 2422 に減った)。
      ペアは入力順に貪欲に採るので、先に来たペアが勝ち、余った頂点は動かない。
    """
    members = {vertex for pair in pairs for vertex in pair}
    union = UnionFind(members)
    skipped = 0
    if blocked:
        # 貪欲なので順序で結果が変わる。入力順に依存しないようソートしておく
        cluster = {vertex: {vertex} for vertex in members}
        for a, b in sorted(pairs):
            root_a, root_b = union.find(a), union.find(b)
            if root_a == root_b:
                continue
            group_a, group_b = cluster[root_a], cluster[root_b]
            if len(group_a) > len(group_b):
                group_a, group_b = group_b, group_a
            if any(other in blocked.get(vertex, ()) for vertex in group_a for other in group_b):
                skipped += 1
                continue
            union.union(root_a, root_b)
            cluster[union.find(root_a)] = group_a | group_b
    else:
        for a, b in pairs:
            union.union(a, b)
    groups = {}
    for vertex in members:
        groups.setdefault(union.find(vertex), []).append(vertex)
    return sorted((sorted(group) for group in groups.values() if len(group) > 1)), skipped
