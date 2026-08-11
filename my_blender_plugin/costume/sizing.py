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

**設計値(公表寸法が見つからなかったもの。実測レンジではない)**

| 項目 | /身長 | 理由 |
|---|---|---|
| ウエスト〜ヒップの落差 | 0.12 | 未計測。背丈37cmとの釣り合いで置いた |
| 袖口 | 0.13 | 手首周りそのものではなく「カフスの開き」。20.5cm 相当 |
| 断面の厚み比(前後/左右) | 0.68 | サイズ表は周長しか持たないので形は別に決める必要がある。1.0(円)にすると衣装がランプシェードに見える |

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
    "shoulder_width": 0.234,  # 肩先から肩先まで(周長ではない)
    "back_length": 0.234,  # 首の後ろからウエストまで
    "sleeve_length": 0.335,  # 肩先から手首まで
    "neck": 0.228,  # 首回り
    "armhole_depth": 0.131,  # 肩先から袖ぐりの底まで
    "cuff": 0.13,  # 袖口の開き(設計値)
}

#: 身長に依存しない無次元のサイズ表項目(比率上書きの対象外)
ABSOLUTE_DEFAULTS = {
    "depth_ratio": 0.68,  # 胴の断面の 前後の厚み / 左右の幅。1.0 なら円(設計値)
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

    table = {
        "height": float(assumed_height),
        "ease": float(ease),
        "ratios": ratios,
        "body": body,  # ゆとり無しの素寸
        "depth_ratio": depth_ratio,
    }
    table.update(eased)  # 以下はゆとり込み(衣装の寸法)
    # 参考値: 断面を円とみなした場合の半径。楕円のときは周長が主
    table["waist_radius"] = circumference_to_radius(eased["waist"])
    table["hip_radius"] = circumference_to_radius(eased["hip"])
    return table
