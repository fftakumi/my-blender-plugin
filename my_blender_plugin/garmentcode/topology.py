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


def pair_groups(pairs):
    """頂点ペアの列を連結成分ごとにまとめて [[頂点, ...], ...] で返す純粋関数

    溶接は「全体 merge by distance」ではなく **このグループ単位** で行う。
    距離ではなくペアの繋がりで決めるので、たまたま近いだけの別パネルは溶接されない。
    出力は決定的(各グループは昇順、グループ同士は先頭の昇順)。
    """
    members = {vertex for pair in pairs for vertex in pair}
    union = UnionFind(members)
    for a, b in pairs:
        union.union(a, b)
    groups = {}
    for vertex in members:
        groups.setdefault(union.find(vertex), []).append(vertex)
    return sorted((sorted(group) for group in groups.values() if len(group) > 1))
