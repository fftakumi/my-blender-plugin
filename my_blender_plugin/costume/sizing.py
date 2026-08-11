"""サイズ表。bpy 非依存。**素体を一切必要としない。**

衣装は「サイズ表 + ゆとり」から決まる。実物の洋裁と同じ。マネキンも素体も作らない。

## 既定値の出どころ(すべて公表されている寸法・製図の規則。特定の素体の実測ではない)

身長 158cm(JIS の身長区分 R)を基準に、身長比へ直して持つ。

| 項目 | 実寸 | /身長 | 出どころ |
|---|---|---|---|
| バスト | 83〜84cm | 0.525 | JIS L 4005 の 9R(バスト83/ヒップ91/身長158)と文化式 参考寸法(B84) |
| ウエスト | 62〜67cm | 0.405 | 文化式 62 / ドレメ式 60 / JIS 系の表 67 の中間を取った |
| ヒップ | 91〜92cm | 0.576 | JIS 9R の 91、文化式 92 |
| 肩幅 | 37cm | 0.234 | 文化式 参考寸法 |
| 背丈(首後ろ〜ウエスト) | 37cm | 0.234 | 文化式 参考寸法 |
| 袖丈(肩先〜手首) | 53cm | 0.335 | 文化式・ドレメ式ともに 53 |
| 首回り | 36cm | 0.228 | 文化式 36 / ドレメ式 36.5 |
| 袖ぐり深さ | 20.7cm | 0.131 | 新文化式の式 **袖ぐり深さ = B/12 + 13.7**(B=84)。同じ資料の 背幅 = B/8+7.4 は表の実値35cmと一致するので式の整合は取れている |
| 前襟ぐり深さ(側頸点→前中心) | 7.36cm | 0.0466 | 新文化式 **前襟ぐり深さ = B/24 + 3.9**(B=83) |
| 後ろ襟ぐり深さ(側頸点→後ろ中心) | 2.35cm | 0.0149 | 新文化式 **後ろ襟ぐり幅(B/24+3.6)の 1/3** |

**製品の仕上がり寸法(人体寸法ではない。9号ブラウスの実寸表)**

人体寸法表には**着丈が存在しない**。ここを埋めないまま `hem_z` を勘で置いたのが
「クロップ丈に見える」の原因だった(経緯は docs/garments.md)。

| 項目 | 9号 実寸 | /身長158 | 出どころ |
|---|---|---|---|
| 着丈(側頸点→裾) | 65cm | 0.411 | marea 事務服サイズ表 ブラウス 9号 |
| 袖丈(肩先→袖口) | 56cm | 0.354 | 同上。**裄丈75 = 肩幅38/2 + 袖丈56** が恒等式として合うので測り方が確定できる |
| 袖口(カフスの周) | 23cm | 0.146 | 同上 |
| 肩幅(製品) | 38cm | 0.2405 | 同上。人体の肩幅37cmに1cm足したもの |

**設計値(公表寸法が見つからなかったもの。実測レンジではない)**

| 項目 | 値 | 理由 |
|---|---|---|
| ウエスト〜ヒップの落差 | 0.12 /H | 未計測。背丈37cmとの釣り合いで置いた |
| 二の腕まわり(袖幅) | 0.210 /H = 33cm | 製品実寸表に袖幅が無い。製図の目安 **袖ぐり寸法 ≒ B/2**(=41.5cm)と **袖幅 ≒ 袖ぐり寸法 × 0.8** から出した。**測った値ではない**。以前は「メッシュに開けた袖ぐりの穴の周長 × 0.78」から出していたが、穴は矩形なので周長が実物より35%大きく、袖が44cmに膨らんでコウモリ袖に見えた |
| 断面の厚み比(前後/左右) | 0.68 | サイズ表は周長しか持たないので形は別に決める必要がある。1.0(円)にすると衣装がランプシェードに見える |
| 肩傾斜 | 20° | 新文化式の 前22°/後18° の平均 |

## 素体を使いたい場合

必須ではないが、自分のアバターに合わせたいときは `overrides` に実寸(`waist_m` など)を
渡す。**素体を測るのは任意の1ステップ**で、生成そのものは素体を必要としない。
"""

import math

#: 身長に対する比率の既定値。上表のとおり公表寸法・製図規則が出どころ。
RATIO_DEFAULTS = {
    "bust": 0.525,  # バスト周
    "waist": 0.405,  # ウエスト周
    "hip": 0.576,  # ヒップ周
    "hip_drop": 0.12,  # ウエストからヒップまでの垂直距離(設計値)
    "shoulder_width": 0.2405,  # 肩先から肩先まで(周長ではない)。製品実寸 38cm
    "back_length": 0.234,  # 首の後ろからウエストまで
    "sleeve_length": 0.354,  # 肩先から袖口まで。製品実寸 56cm
    "neck": 0.228,  # 首回り
    "armhole_depth": 0.131,  # 肩先から袖ぐりの底まで
    "cuff": 0.146,  # 袖口(カフス)の周。製品実寸 23cm
    "bicep": 0.210,  # 二の腕まわり(袖幅×2)。33cm 相当(設計値。下の表を見ること)
    "garment_length": 0.411,  # 着丈(側頸点→裾)。製品実寸 65cm
    "front_neck_drop": 0.0466,  # 側頸点から前中心までの落差(前襟ぐり深さ)
    "back_neck_drop": 0.0149,  # 側頸点から後ろ中心までの落差(後ろ襟ぐり深さ)
    # 頭囲。公表値(AIST 人体寸法データベースの成人女性 約55cm)を身長158cmで
    # 割って丸めた**設計値**。フードのかぶりに使う。実測はしていない
    "head_circumference": 0.35,
    # 全頭高(頭頂〜顎)。公表値(同 約22cm)を丸めた**設計値**
    "head_height": 0.14,
}

#: 身長に依存しない無次元のサイズ表項目(比率上書きの対象外)
ABSOLUTE_DEFAULTS = {
    "depth_ratio": 0.68,  # 胴の断面の 前後の厚み / 左右の幅。1.0 なら円(設計値)
    "shoulder_slope_degrees": 20.0,  # 肩の傾き。新文化式 前22°/後18° の平均
}

#: ゆとりを掛ける項目(周長のみ。幅・長さ・垂直距離には掛けない)
_EASED_FIELDS = frozenset(("bust", "waist", "hip", "neck"))


class SizingError(ValueError):
    """サイズ表の指定が壊れているときに投げる"""


def circumference_to_radius(circumference):
    """周長 → 半径"""
    return circumference / (2.0 * math.pi)


def radius_to_circumference(radius):
    """半径 → 周長"""
    return radius * 2.0 * math.pi


def resolve(assumed_height, overrides=None, ease=0.0):
    """身長と上書き指定から実寸(メートル)のサイズ表を作る。

    overrides のキーは2通り書ける:
      - ``"waist": 0.40``   … 身長に対する比率で上書き
      - ``"waist_m": 0.62`` … メートルの実寸で上書き(比率指定より優先)

    ease(ゆとり)は周長にだけ掛かる。返り値には ease 適用前の素寸も入れて、
    レポートで「素寸 / ゆとり込み」を両方出せるようにする。
    """
    if assumed_height <= 0:
        raise SizingError("assumed_height は正の数にしてください: %r" % (assumed_height,))
    if ease < 0:
        raise SizingError("ease は 0 以上にしてください: %r" % (ease,))

    overrides = dict(overrides or {})
    unknown = [
        key
        for key in overrides
        if key not in RATIO_DEFAULTS
        and key not in ABSOLUTE_DEFAULTS
        and not (key.endswith("_m") and key[:-2] in RATIO_DEFAULTS)
    ]
    if unknown:
        raise SizingError(
            "sizing に未知のキーがあります: %s(使えるのは %s とその _m 付き、および %s)"
            % (
                ", ".join(sorted(unknown)),
                ", ".join(sorted(RATIO_DEFAULTS)),
                ", ".join(sorted(ABSOLUTE_DEFAULTS)),
            )
        )

    ratios = {}
    body = {}
    for field, default_ratio in RATIO_DEFAULTS.items():
        absolute = overrides.get(field + "_m")
        if absolute is not None:
            value = float(absolute)
            if value <= 0:
                raise SizingError("sizing.%s_m は正の数にしてください: %r" % (field, absolute))
            ratios[field] = value / assumed_height
        else:
            ratio = float(overrides.get(field, default_ratio))
            if ratio <= 0:
                raise SizingError("sizing.%s は正の数にしてください: %r" % (field, ratio))
            ratios[field] = ratio
            value = ratio * assumed_height
        body[field] = value

    eased = {
        field: value * (1.0 + ease) if field in _EASED_FIELDS else value
        for field, value in body.items()
    }

    depth_ratio = float(overrides.get("depth_ratio", ABSOLUTE_DEFAULTS["depth_ratio"]))
    if not 0.0 < depth_ratio <= 1.0:
        raise SizingError(
            "sizing.depth_ratio は 0 より大きく 1 以下にしてください(1.0 = 円): %r"
            % (depth_ratio,)
        )
    slope = float(
        overrides.get("shoulder_slope_degrees", ABSOLUTE_DEFAULTS["shoulder_slope_degrees"])
    )
    if not 0.0 <= slope < 80.0:
        raise SizingError(
            "sizing.shoulder_slope_degrees は 0 以上 80 未満にしてください: %r" % (slope,)
        )

    table = {
        "height": float(assumed_height),
        "ease": float(ease),
        "ratios": ratios,
        "body": body,  # ゆとり無しの素寸
        "depth_ratio": depth_ratio,
        "shoulder_slope_degrees": slope,
    }
    table.update(eased)  # 以下はゆとり込み(衣装の寸法)
    # 参考値: 断面を円とみなした場合の半径。楕円のときは周長が主
    table["waist_radius"] = circumference_to_radius(eased["waist"])
    table["hip_radius"] = circumference_to_radius(eased["hip"])
    return table
