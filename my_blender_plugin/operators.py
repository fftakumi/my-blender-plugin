import hashlib

import bpy

from .costume import ai_bridge, parts as costume_parts, spec as costume_spec, validate


def grid_positions(count, spacing):
    """count x count のグリッド配置座標(原点中心)のリストを返す純粋関数"""
    offset = (count - 1) * spacing / 2
    return [
        (x * spacing - offset, y * spacing - offset, 0.0)
        for x in range(count)
        for y in range(count)
    ]


def resolve_body_and_corset(selected, active_name):
    """選択オブジェクト [(名前, タイプ), ...] とアクティブ名から (素体名, コルセット名, エラー文言) を返す純粋関数

    コルセット=アクティブオブジェクト、素体=もう一方。成功時はエラー文言が None。
    """
    if active_name is None:
        return None, None, "アクティブオブジェクトがありません。コルセットをアクティブにしてください"
    if len(selected) != 2:
        return None, None, (
            "素体とコルセットの2オブジェクトだけを選択してください(現在 %d 個)" % len(selected)
        )
    names = [name for name, _ in selected]
    if active_name not in names:
        return None, None, "アクティブオブジェクト(コルセット)を選択に含めてください"
    non_mesh = [name for name, obj_type in selected if obj_type != "MESH"]
    if non_mesh:
        return None, None, "メッシュ以外が選択されています: " + ", ".join(non_mesh)
    body_name = next(name for name in names if name != active_name)
    return body_name, active_name, None


def bounding_dimensions(points):
    """点群 [(x, y, z), ...] のバウンディングボックス寸法 (dx, dy, dz) を返す純粋関数"""
    if not points:
        raise ValueError("点が1つもありません")
    return tuple(
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    )


# 影響範囲と縁のなじませ幅の自動値は、コルセット自身の大きさを基準にする
# (シーンのスケールが 1単位=1cm でも 1単位=1m でも同じ操作感になるように)
_AUTO_RANGE_RATIO = 0.5
_AUTO_RIM_BAND_RATIO = 0.1


def resolve_fit_ranges(max_range, rim_band, corset_dimensions):
    """0 指定を「コルセットの大きさから自動」に解決して (影響範囲, 縁のなじませ幅) を返す純粋関数"""
    if max_range < 0:
        raise ValueError("max_range は 0 以上で指定してください")
    if rim_band < 0:
        raise ValueError("rim_band は 0 以上で指定してください")
    size = max(corset_dimensions)
    if size <= 0:
        raise ValueError("コルセットの大きさが 0 です")
    return (
        max_range if max_range > 0 else size * _AUTO_RANGE_RATIO,
        rim_band if rim_band > 0 else size * _AUTO_RIM_BAND_RATIO,
    )


def coverage_weight(distance_to_surface, distance_to_rim, max_range, rim_band):
    """コルセットが担当する範囲かどうかを表すウェイト(0.0〜1.0)を返す純粋関数

    はみ出しの深さではウェイトを落とさない。「表面のどちら側にいるか」で
    押し込むかどうかを決めるのは Shrinkwrap の INSIDE 側で、そちらは深さを
    問わないため、ここで距離によって弱めると深いはみ出しが取り残される。

    ウェイトを落とすのは次の2つだけ:
      - 影響範囲 max_range の外(遠く離れた手足を巻き込まないための上限)
      - コルセットの開いた縁(裾・胸元)から rim_band 以内。縁の外側には
        押し込む先の面が無く、そのまま全力で寄せると縁に引きつれる
    """
    if distance_to_surface > max_range:
        return 0.0
    if rim_band <= 0 or distance_to_rim >= rim_band:
        return 1.0
    if distance_to_rim <= 0:
        return 0.0
    ratio = distance_to_rim / rim_band
    return ratio * ratio * (3.0 - 2.0 * ratio)  # smoothstep


# Blender の名前(モディファイア・頂点グループ・シェイプキー)は最大63バイトで、
# 超えると黙って切り詰められ、以後の名前検索がすべて失敗する
_NAME_MAX_BYTES = 63
_LONGEST_FIT_PREFIX = "MYPLUGIN_FitVWProx_"


def fit_name_token(corset_name):
    """コルセット名から、63バイト制限内に収まる決定的なトークンを作る純粋関数

    長すぎる名前はバイト境界で切り詰め、衝突しないよう短いハッシュを付ける。
    """
    budget = _NAME_MAX_BYTES - len(_LONGEST_FIT_PREFIX.encode("utf-8"))
    encoded = corset_name.encode("utf-8")
    if len(encoded) <= budget:
        return corset_name
    digest = hashlib.sha1(encoded).hexdigest()[:6]
    keep = budget - 7  # "_" + ハッシュ6桁ぶんを確保
    truncated = encoded[:keep].decode("utf-8", errors="ignore")
    return truncated + "_" + digest


def fit_names(corset_name):
    """コルセット名から、フィットに使う頂点グループ・モディファイア・シェイプキーの名前を返す純粋関数

    決定的な命名にして、再実行時に前回分を見つけて削除できるようにする。
    """
    token = fit_name_token(corset_name)
    return {
        "vertex_group": "myplugin_fit_" + token,
        # vwp は現在は作らない。旧バージョンが積んだ VertexWeightProximity を
        # 再実行時に取り除くためだけに名前を残している
        "vwp": "MYPLUGIN_FitVWProx_" + token,
        "shrinkwrap": "MYPLUGIN_FitShrink_" + token,
        "smooth": "MYPLUGIN_FitSmooth_" + token,
        "shapekey": "Fit_" + token,
    }


def modifier_insert_index(modifier_types):
    """既存モディファイアのタイプ一覧から、シェイプキー焼き込み用の挿入位置を返す純粋関数

    焼き込みはレスト空間で確定させる必要があるため、最初の Armature の直前
    (無ければ末尾)に挿入する。モディファイアとして残す場合はポーズ後の
    コルセットに追従させるため末尾のまま(この関数は使わない)。
    """
    for index, modifier_type in enumerate(modifier_types):
        if modifier_type == "ARMATURE":
            return index
    return len(modifier_types)


class MYPLUGIN_OT_hello(bpy.types.Operator):
    """動作確認用のサンプルオペレーター"""

    bl_idname = "myplugin.hello"
    bl_label = "Hello"
    bl_description = "インフォエリアにメッセージを表示する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "Hello from My Blender Plugin!")
        return {"FINISHED"}


class MYPLUGIN_OT_add_cube_grid(bpy.types.Operator):
    """キューブをグリッド状に並べるサンプルオペレーター"""

    bl_idname = "myplugin.add_cube_grid"
    bl_label = "Add Cube Grid"
    bl_description = "キューブをグリッド状に配置する"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(
        name="Count",
        description="1辺あたりの個数",
        default=3,
        min=1,
        max=20,
    )
    spacing: bpy.props.FloatProperty(
        name="Spacing",
        description="キューブ同士の間隔",
        default=3.0,
        min=0.1,
    )

    def execute(self, context):
        for location in grid_positions(self.count, self.spacing):
            bpy.ops.mesh.primitive_cube_add(location=location)
        return {"FINISHED"}


class MYPLUGIN_OT_fit_body_to_corset(bpy.types.Operator):
    """素体メッシュをコルセット形状に合わせて締め付け変形するモディファイア一式を構築する"""

    bl_idname = "myplugin.fit_body_to_corset"
    bl_label = "Fit Body to Corset"
    bl_description = (
        "素体を選択→コルセットをCtrl+クリックしてから実行。"
        "コルセットの面から見て外側(法線側)にはみ出た部分を、はみ出しの深さに"
        "関係なく内側へ押し込むモディファイア一式(Shrinkwrap→CorrectiveSmooth)"
        "を素体に構築する。レストポーズで実行すること"
    )
    bl_options = {"REGISTER", "UNDO"}

    max_range: bpy.props.FloatProperty(
        name="影響範囲",
        description=(
            "コルセット表面からこの距離までの頂点を対象にする。"
            "はみ出しの強さではなく、離れた手足を巻き込まないための上限。"
            "0でコルセットの大きさから自動決定"
        ),
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
    )
    rim_band: bpy.props.FloatProperty(
        name="縁のなじませ幅",
        description=(
            "コルセットの開いた縁(裾・胸元)からこの幅ぶんは効果を0へなじませ、"
            "縁での引きつれを防ぐ。0でコルセットの大きさから自動決定"
        ),
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
    )
    offset: bpy.props.FloatProperty(
        name="オフセット",
        description="コルセット表面からさらに内側へ押し込む距離(食い込みの余裕)",
        default=0.001,
        subtype="DISTANCE",
    )
    smooth_factor: bpy.props.FloatProperty(
        name="スムーズ強度",
        description="変形境界を滑らかにする Corrective Smooth の強さ",
        default=0.5,
        min=0.0,
        max=1.0,
    )
    smooth_iterations: bpy.props.IntProperty(
        name="スムーズ反復",
        description="Corrective Smooth の反復回数",
        default=5,
        min=0,
        max=30,
    )
    as_shapekey: bpy.props.BoolProperty(
        name="シェイプキーとして焼き込む",
        description="モディファイアを残さず、変形をシェイプキー Fit_<コルセット名> に焼き込む(衣装トグルと連動させる場合に便利)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and context.active_object is not None
            and context.active_object.type == "MESH"
        )

    def execute(self, context):
        selected = [(obj.name, obj.type) for obj in context.selected_objects]
        active = context.active_object
        body_name, corset_name, error = resolve_body_and_corset(
            selected, active.name if active else None
        )
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}

        body = bpy.data.objects[body_name]
        corset = bpy.data.objects[corset_name]
        if self.as_shapekey and body.data.users > 1:
            self.report(
                {"ERROR"},
                "素体メッシュがマルチユーザー(リンク複製)のためシェイプキーに焼き込めません。"
                "オブジェクト > 関係 > シングルユーザー化 を先に行ってください",
            )
            return {"CANCELLED"}
        names = fit_names(corset_name)

        self._remove_previous(body, names)

        try:
            entries, used_range, used_band = self._coverage_weights(
                context, body, corset, self.max_range, self.rim_band
            )
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, "影響範囲の計算に失敗しました: %s" % error)
            return {"CANCELLED"}
        if not entries:
            self.report(
                {"ERROR"},
                "コルセットの影響範囲に素体の頂点が1つも入りませんでした。"
                "影響範囲を広げるか、コルセットの位置を確認してください",
            )
            return {"CANCELLED"}

        vertex_group = body.vertex_groups.new(name=names["vertex_group"])
        full = [index for index, weight in entries if weight >= 1.0]
        vertex_group.add(full, 1.0, "REPLACE")
        for index, weight in entries:
            if weight < 1.0:
                vertex_group.add([index], weight, "REPLACE")

        insert_at = modifier_insert_index([m.type for m in body.modifiers])
        stack_len_before = len(body.modifiers)

        shrinkwrap = body.modifiers.new(names["shrinkwrap"], "SHRINKWRAP")
        shrinkwrap.target = corset
        shrinkwrap.wrap_method = "NEAREST_SURFACEPOINT"
        shrinkwrap.wrap_mode = "INSIDE"
        shrinkwrap.vertex_group = vertex_group.name
        shrinkwrap.offset = self.offset

        smooth = body.modifiers.new(names["smooth"], "CORRECTIVE_SMOOTH")
        smooth.factor = self.smooth_factor
        smooth.iterations = self.smooth_iterations
        smooth.vertex_group = vertex_group.name

        # modifier_move_to_index などの bpy.ops は素体をアクティブにして実行する必要がある
        prev_active = context.view_layer.objects.active
        prev_selected = list(context.selected_objects)
        for obj in prev_selected:
            obj.select_set(False)
        body.select_set(True)
        context.view_layer.objects.active = body
        try:
            # 焼き込み時のみ Armature より前(レスト空間)へ移動する。
            # モディファイアとして残す場合は末尾のまま: Shrinkwrap のターゲットは
            # 評価済み(ポーズ後)のコルセットなので、Armature の後ろに置かないと
            # ポーズを付けた時にレスト座標をポーズ後のコルセットへ投影して破綻する
            if self.as_shapekey and insert_at < stack_len_before:
                for offset_index, mod_name in enumerate(
                    (names["shrinkwrap"], names["smooth"])
                ):
                    bpy.ops.object.modifier_move_to_index(
                        modifier=mod_name, index=insert_at + offset_index
                    )
            if self.as_shapekey:
                try:
                    self._bake_as_shapekey(body, names)
                except RuntimeError as error:
                    self.report(
                        {"ERROR"},
                        "シェイプキーへの焼き込みに失敗しました: %s" % error,
                    )
                    return {"CANCELLED"}
        finally:
            body.select_set(False)
            for obj in prev_selected:
                obj.select_set(True)
            context.view_layer.objects.active = prev_active

        if self.as_shapekey:
            self.report(
                {"INFO"},
                "シェイプキー '%s' に締め付けを焼き込みました" % names["shapekey"],
            )
        else:
            self.report(
                {"INFO"},
                "%s に %d 頂点ぶんのコルセットフィットを構築しました"
                "(影響範囲 %.3f / 縁のなじませ %.3f)"
                % (body_name, len(entries), used_range, used_band),
            )
        return {"FINISHED"}

    def _remove_previous(self, body, names):
        """再実行時に前回のフィット結果(モディファイア・頂点グループ・シェイプキー)を消す"""
        for mod_name in (names["vwp"], names["shrinkwrap"], names["smooth"]):
            modifier = body.modifiers.get(mod_name)
            if modifier is not None:
                body.modifiers.remove(modifier)
        vertex_group = body.vertex_groups.get(names["vertex_group"])
        if vertex_group is not None:
            body.vertex_groups.remove(vertex_group)
        shape_keys = body.data.shape_keys
        if shape_keys is not None and names["shapekey"] in shape_keys.key_blocks:
            body.shape_key_remove(shape_keys.key_blocks[names["shapekey"]])

    def _coverage_weights(self, context, body, corset, max_range, rim_band):
        """コルセットが担当する範囲の [(頂点インデックス, ウェイト), ...] と、解決後の範囲を返す

        Shrinkwrap の INSIDE がコルセットの法線を見て「外側にある頂点だけを、
        深さに関係なく表面まで押し込む」処理をしてくれるので、この頂点グループは
        「どこをコルセットに任せるか」だけを決める。深さでウェイトを落とさない。
        """
        # テスト用のフェイク bpy 環境には mathutils が無いため、ここで遅延import する
        from mathutils.bvhtree import BVHTree
        from mathutils.geometry import intersect_point_line

        depsgraph = context.evaluated_depsgraph_get()

        corset_eval = corset.evaluated_get(depsgraph)
        corset_mesh = corset_eval.to_mesh()
        try:
            matrix = corset_eval.matrix_world
            points = [matrix @ vertex.co for vertex in corset_mesh.vertices]
            polygons = [list(polygon.vertices) for polygon in corset_mesh.polygons]
        finally:
            corset_eval.to_mesh_clear()
        if not polygons:
            raise RuntimeError("コルセットに面がありません")

        max_range, rim_band = resolve_fit_ranges(
            max_range, rim_band, bounding_dimensions(points)
        )

        # 1枚の面にしか使われていない辺 = コルセットの開いた縁(裾・胸元・背中の合わせ)
        edge_users = {}
        for polygon in polygons:
            for position, index in enumerate(polygon):
                key = tuple(sorted((index, polygon[position - 1])))
                edge_users[key] = edge_users.get(key, 0) + 1
        rim_edges = [
            (points[a], points[b]) for (a, b), users in edge_users.items() if users == 1
        ]
        tree = BVHTree.FromPolygons(points, polygons)

        def rim_distance(point):
            """point からコルセットの開いた縁までの最短距離(縁が無ければ無限大)"""
            nearest = float("inf")
            for start, end in rim_edges:
                closest, factor = intersect_point_line(point, start, end)
                if factor <= 0.0:
                    closest = start
                elif factor >= 1.0:
                    closest = end
                nearest = min(nearest, (point - closest).length)
            return nearest

        body_eval = body.evaluated_get(depsgraph)
        body_mesh = body_eval.to_mesh()
        try:
            if len(body_mesh.vertices) != len(body.data.vertices):
                raise RuntimeError(
                    "評価後の頂点数(%d)が元メッシュ(%d)と一致しません。"
                    "Mirror や Subdivision など頂点数を変えるモディファイアを"
                    "適用または一時無効化してから実行してください"
                    % (len(body_mesh.vertices), len(body.data.vertices))
                )
            body_matrix = body_eval.matrix_world
            coordinates = [body_matrix @ vertex.co for vertex in body_mesh.vertices]
        finally:
            body_eval.to_mesh_clear()

        # BVH 探索は重いので、コルセットのバウンディングボックスを影響範囲ぶん
        # 広げた箱の外にある頂点(頭・手足など大半)は先に捨てる
        lower = [min(point[axis] for point in points) - max_range for axis in range(3)]
        upper = [max(point[axis] for point in points) + max_range for axis in range(3)]

        entries = []
        for index, point in enumerate(coordinates):
            if any(
                point[axis] < lower[axis] or point[axis] > upper[axis]
                for axis in range(3)
            ):
                continue
            location, _normal, _polygon_index, distance = tree.find_nearest(
                point, max_range
            )
            if location is None:
                continue
            weight = coverage_weight(
                distance,
                rim_distance(location) if rim_band > 0 else float("inf"),
                max_range,
                rim_band,
            )
            if weight > 0.0:
                entries.append((index, weight))
        return entries, max_range, rim_band

    def _bake_as_shapekey(self, body, names):
        """構築したモディファイアをシェイプキー Fit_<コルセット名> 1本に焼き込む(要: bodyがアクティブ)"""
        # 表情など既存シェイプキーの現在値が焼き込み結果に混入しないよう、
        # 焼き込みの間はすべて 0 にして、終了後(失敗時も)に元へ戻す
        saved_key_values = {}
        shape_keys = body.data.shape_keys
        if shape_keys is not None:
            for key_block in shape_keys.key_blocks:
                saved_key_values[key_block.name] = key_block.value
                key_block.value = 0.0
        try:
            # シェイプキーはモディファイアより先に評価されるため、Shrinkwrap のキーを 1.0 に
            # した状態で Smooth を焼くと「締め付け+スムーズ」の合成結果が最終キーに入る
            shrink_key = self._apply_as_shapekey(body, names["shrinkwrap"])
            shrink_key.value = 1.0
            final_key = self._apply_as_shapekey(body, names["smooth"])
            body.shape_key_remove(shrink_key)
            final_key.name = names["shapekey"]
            final_key.value = 1.0
        finally:
            shape_keys = body.data.shape_keys
            if shape_keys is not None:
                for key_block in shape_keys.key_blocks:
                    if key_block.name in saved_key_values:
                        key_block.value = saved_key_values[key_block.name]

    def _apply_as_shapekey(self, body, modifier_name):
        """モディファイアをシェイプキーとして適用し、生成されたキーを返す"""
        bpy.ops.object.modifier_apply_as_shapekey(
            modifier=modifier_name, keep_modifier=False
        )
        return body.data.shape_keys.key_blocks[modifier_name]


def _preset_items():
    """spec プリセットを EnumProperty の項目にする"""
    names = costume_spec.list_presets()
    return [(name, name, "同梱プリセット %s" % name) for name in names] or [
        ("skirt_flare", "skirt_flare", "既定")
    ]


class MYPLUGIN_OT_generate_costume(bpy.types.Operator):
    """説明文またはプリセットから衣装メッシュとマテリアルを生成する"""

    bl_idname = "myplugin.generate_costume"
    bl_label = "衣装を生成"
    bl_description = (
        "説明文(例:「紺のプリーツミニスカート」)またはプリセットから、"
        "衣装単体のメッシュを生成してマテリアルまで割り当てる。"
        "素体は不要。コレクション Costume_<名前> に入る"
    )
    bl_options = {"REGISTER", "UNDO"}

    source: bpy.props.EnumProperty(
        name="入力",
        description="説明文から組み立てるか、同梱プリセットを使うか",
        items=[
            ("TEXT", "説明文", "日本語・英語の説明文から組み立てる"),
            ("PRESET", "プリセット", "同梱の spec をそのまま使う"),
        ],
        default="TEXT",
    )
    description: bpy.props.StringProperty(
        name="説明",
        description="作りたい衣装の説明。例:「紺の24本プリーツのひざ丈スカート」",
        default="紺のプリーツミニスカート",
    )
    preset: bpy.props.EnumProperty(
        name="プリセット", description="同梱の spec", items=lambda self, context: _preset_items()
    )
    image_path: bpy.props.StringProperty(
        name="参考画像",
        description=(
            "指定すると画像から色を取ってマテリアルに反映する(空なら説明文の色を使う)。"
            "取るのは色だけで、形は説明文/プリセットが決める"
        ),
        default="",
        subtype="FILE_PATH",
    )
    image_colors: bpy.props.IntProperty(
        name="画像から取る色数",
        description="1色目を主色にし、残りは accent マテリアルとして足す",
        default=3,
        min=1,
        max=8,
    )
    use_ai: bpy.props.BoolProperty(
        name="AIに解釈させる(claude -p)",
        description=(
            "説明文の解釈を claude -p に任せる(辞書より多様な衣装が作れる)。"
            "応答を待つ間 UI が固まる(最大約120秒、作り直しが入ると約240秒)。"
            "claude CLI が無い環境では待たずに辞書の結果へフォールバックする。"
            "オフにすると外部プロセスを起動せず、キーワード辞書だけで解釈する"
        ),
        default=True,
    )
    meters_per_unit: bpy.props.FloatProperty(
        name="1unitのメートル数",
        description="シーンのスケール。1unit=1cm のシーンなら 0.01",
        default=1.0,
        min=1e-6,
    )
    assumed_height: bpy.props.FloatProperty(
        name="想定身長(m)",
        description="寸法の基準。単体の衣装に身長は無いのでここで明示する",
        default=1.53,
        min=0.1,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def invoke(self, context, event):
        # シーンの単位設定から埋める。1unit=1cm のシーン(scale_length 0.01)で
        # 既定の 1.0 のまま押すと身長1.53"cm"の衣装ができて見つからなくなる
        scale = getattr(context.scene.unit_settings, "scale_length", 1.0)
        if scale:
            self.meters_per_unit = scale
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        # build / image_input は bmesh / bpy.data を使うので、フェイク bpy のテスト環境で
        # import されないようここで遅延 import する(_coverage_weights と同じ理由)
        from .costume import build as costume_build, image_input

        try:
            # 身長はダイアログ値が既定。説明文に明記されていた場合だけそちらが勝つ
            # (以前は無条件でダイアログ値が上書きし、「身長160cm」が死んでいた)
            height = self.assumed_height
            if self.source == "PRESET":
                spec = costume_spec.load_preset(self.preset)
                origin = "プリセット %s" % self.preset
            else:
                result = ai_bridge.spec_from_text(
                    self.description,
                    runner=None if self.use_ai else _refuse_ai,
                )
                spec = result["spec"]
                origin = "説明文(%s)" % result["source"]
                for note in result["parse"]["notes"]:
                    self.report({"INFO"}, note)
                for item in result["unsupported"]:
                    self.report({"INFO"}, "AI: %s(spec からは省いた)" % item)
                if result["fallback_warning"]:
                    self.report({"WARNING"}, result["fallback_warning"])
                elif result["ai_error"]:
                    self.report({"WARNING"}, "AI に頼れませんでした: %s" % result["ai_error"])
                if result["explicit_height"] is not None:
                    height = result["explicit_height"]
                    self.report(
                        {"INFO"},
                        "説明文の身長 %.2fm を使う(ダイアログの値より優先)" % height,
                    )
            if self.image_path.strip():
                for note in image_input.apply_image(
                    spec, self.image_path, count=self.image_colors
                ):
                    self.report({"INFO"}, note)
                origin += " + 画像の色"
            spec["meters_per_unit"] = self.meters_per_unit
            spec["assumed_height"] = height
            spec = costume_spec.normalize_spec(spec)
        except (costume_spec.SpecError, image_input.ImageInputError, ValueError) as error:
            self.report({"ERROR"}, "spec を組み立てられませんでした: %s" % error)
            return {"CANCELLED"}

        try:
            created = costume_build.build_costume(spec, context.scene)
        except (costume_parts.PartError, ValueError, RuntimeError) as error:
            self.report({"ERROR"}, "生成に失敗しました: %s" % error)
            return {"CANCELLED"}

        report = validate.costume_report(created["built"], spec)
        message = "%s から %s: %s(頂点 %d / 面 %d / エッジ長比 %.4f)" % (
            origin,
            created["collection"].name,
            report["verdict"],
            report["totals"]["verts"],
            report["totals"]["faces"],
            report["edge_length_over_h"] or 0.0,
        )
        if report["failed"]:
            self.report({"WARNING"}, message + " 不合格: " + ", ".join(report["failed"]))
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


def _refuse_ai(_prompt):
    """AI を使わない設定のときに request_spec を確実に失敗させる"""
    raise ai_bridge.AIBridgeError(
        "AI 利用をオフにしているため、キーワード辞書だけで解釈しました"
    )


_classes = (
    MYPLUGIN_OT_hello,
    MYPLUGIN_OT_add_cube_grid,
    MYPLUGIN_OT_fit_body_to_corset,
    MYPLUGIN_OT_generate_costume,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
