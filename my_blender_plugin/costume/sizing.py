"""サイズ表。bpy 非依存。

素体にフィットさせない代わりの基準。実物の洋裁と同じで、衣装は「サイズ表 + ゆとり」から
決まる。**マネキンメッシュは作らない**(素体を用意してそれに合っているか採点するのは
「衣装単体で作る」という前提に反する)。

数値の出どころ:
  - `waist` / `hip` の既定比は、手元の実機アバター **1体** (身長 1.612m) の
    ガイドリング実測から出した: ウエスト周 0.608m = 0.377H / ヒップ周 0.960m = 0.596H。
  - 既定値 0.39 / 0.58 はそれを丸めた**設計値**であって、複数体を測った「実測レンジ」ではない。
    レンジとして語れる数値は持っていない(未計測)。
  - `hip_drop` は未計測。0.12H は設計値。
  - `depth_ratio`(胴の断面の 前後の厚み / 左右の幅)は同じ1体を高さ別に実測した:
    ウエスト(z/H 0.62) **0.646** / 腹(0.58) 0.647 / 腰上(0.56) 0.609 / ヒップ(0.50) 0.471。
    既定 0.68 はウエスト実測を丸めた設計値(この個体は意図的に腰が広いので、
    そのままだと一般形として極端になる)。**円ではない**ことがこの実測の要点。

必要になった部位(バスト・肩幅・袖丈など)は、それを使うパーツを実装するときに足す。
測っていない値を先に並べても根拠が無いので置かない。
"""

import math

#: 身長に対する比率の既定値。上記のとおり実測レンジではなく設計値。
RATIO_DEFAULTS = {
    "waist": 0.39,  # ウエスト周 / H
    "hip": 0.58,  # ヒップ周 / H
    "hip_drop": 0.12,  # ウエストからヒップまでの垂直距離 / H
}

#: 身長に依存しない無次元のサイズ表項目(比率上書きの対象外)
ABSOLUTE_DEFAULTS = {
    "depth_ratio": 0.68,  # 胴の断面の 前後の厚み / 左右の幅。1.0 なら円
}

#: ゆとりを掛ける項目(周長のみ。垂直距離には掛けない)
_EASED_FIELDS = frozenset(("waist", "hip"))


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

    return {
        "height": float(assumed_height),
        "ease": float(ease),
        "ratios": ratios,
        "body": body,  # ゆとり無しの素寸
        "depth_ratio": depth_ratio,
        "waist": eased["waist"],  # 以下はゆとり込み(衣装の寸法)
        "hip": eased["hip"],
        "hip_drop": eased["hip_drop"],
        # 参考値: 断面を円とみなした場合の半径。楕円のときは周長が主
        "waist_radius": circumference_to_radius(eased["waist"]),
        "hip_radius": circumference_to_radius(eased["hip"]),
    }
