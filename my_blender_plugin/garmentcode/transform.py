"""GarmentCode 座標系 -> Blender 座標系の変換(bpy 非依存)。

GarmentCode: Y-up・正面 +Z / Blender シーン: Z-up・正面 -Y

    M        = Rx(+90 度) @ Euler_XYZ(rotation)
    location = (tx, -tz, ty)

`panel["rotation"]` は **度数の外因性 Euler XYZ**(Blender 既定の 'XYZ' と同じ並び。
X を先に掛けるので行列は Rz @ Ry @ Rx)。単位はどちらも cm なのでスケーリングは不要。

`mathutils.Matrix` は使わない。行列は「行のタプル3本」として素の Python で持つ
(フェイク bpy のテスト環境に mathutils が無いため)。bpy 側は
`Matrix(matrix)` にそのまま渡せる。
"""

import math


def euler_xyz_matrix(rx, ry, rz):
    """外因性 Euler XYZ(ラジアン)の回転行列 Rz @ Ry @ Rx を行タプルで返す純粋関数"""
    ci, si = math.cos(rx), math.sin(rx)
    cj, sj = math.cos(ry), math.sin(ry)
    ch, sh = math.cos(rz), math.sin(rz)
    return (
        (cj * ch, sj * si * ch - ci * sh, sj * ci * ch + si * sh),
        (cj * sh, sj * si * sh + ci * ch, sj * ci * sh - si * ch),
        (-sj, cj * si, cj * ci),
    )


def matmul(a, b):
    """3x3 行列の積(行タプル)を返す純粋関数"""
    return tuple(
        tuple(sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3))
        for row in range(3)
    )


# GarmentCode の Y-up を Blender の Z-up へ倒す固定回転
RX90 = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
)


def panel_transform(panel):
    """パネルの (回転行列, 位置) を返す純粋関数。行列は行タプル3本"""
    rx, ry, rz = (math.radians(angle) for angle in panel["rotation"])
    matrix = matmul(RX90, euler_xyz_matrix(rx, ry, rz))
    tx, ty, tz = panel["translation"]
    return matrix, (tx, -tz, ty)


def apply(matrix, location, point2d):
    """パネル座標の 2D 点を Blender のワールド座標 (x, y, z) へ写す純粋関数"""
    x, y = point2d[0], point2d[1]
    return (
        matrix[0][0] * x + matrix[0][1] * y + location[0],
        matrix[1][0] * x + matrix[1][1] * y + location[1],
        matrix[2][0] * x + matrix[2][1] * y + location[2],
    )


def place_panel(panel, points2d):
    """パネルの 2D 点列をまとめて Blender 座標へ写した 3D 点列を返す純粋関数"""
    matrix, location = panel_transform(panel)
    return [apply(matrix, location, point) for point in points2d]
