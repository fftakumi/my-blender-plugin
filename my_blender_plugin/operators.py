import bpy


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


def proximity_distances(fit_distance, falloff):
    """フィット距離と減衰幅から VertexWeightProximity の (min_dist, max_dist) を返す純粋関数

    はみ出した頂点はコルセット表面から「はみ出しの深さ」ぶん離れているため、
    距離0でだけウェイト1にすると深いはみ出しほど押し込まれなくなる。
    max_dist(=フィット距離)以下でウェイトを1.0に飽和させ、そこから falloff ぶん
    離れたところで0になるようにする。min > max の逆転指定で「近いほど強い」。
    """
    if fit_distance <= 0:
        raise ValueError("fit_distance は正の値で指定してください")
    if falloff < 0:
        raise ValueError("falloff は 0 以上で指定してください")
    return (fit_distance + falloff, fit_distance)


def fit_names(corset_name):
    """コルセット名から、フィットに使う頂点グループ・モディファイア・シェイプキーの名前を返す純粋関数

    決定的な命名にして、再実行時に前回分を見つけて削除できるようにする。
    """
    return {
        "vertex_group": "myplugin_fit_" + corset_name,
        "vwp": "MYPLUGIN_FitVWProx_" + corset_name,
        "shrinkwrap": "MYPLUGIN_FitShrink_" + corset_name,
        "smooth": "MYPLUGIN_FitSmooth_" + corset_name,
        "shapekey": "Fit_" + corset_name,
    }


def modifier_insert_index(modifier_types):
    """既存モディファイアのタイプ一覧から、フィット用モディファイアの挿入位置を返す純粋関数

    Armature より後ろに置くとポーズ空間で締め付けが評価されて破綻するため、
    最初の Armature の直前(無ければ末尾)に挿入する。
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
        "コルセットからはみ出た部分を内側へ押し込むモディファイア一式"
        "(VertexWeightProximity→Shrinkwrap→CorrectiveSmooth)を素体に構築する。"
        "レストポーズで実行すること"
    )
    bl_options = {"REGISTER", "UNDO"}

    fit_distance: bpy.props.FloatProperty(
        name="フィット距離",
        description="コルセットからこの距離以内の頂点は全力(ウェイト1.0)で押し込む。はみ出しの最大の深さより大きくすること",
        default=0.03,
        min=0.0001,
        subtype="DISTANCE",
    )
    falloff: bpy.props.FloatProperty(
        name="減衰幅",
        description="フィット距離の外側で、変形の影響がなだらかに消えるまでの追加距離",
        default=0.03,
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
        names = fit_names(corset_name)

        self._remove_previous(body, names)

        vertex_group = body.vertex_groups.new(name=names["vertex_group"])
        vertex_group.add(list(range(len(body.data.vertices))), 1.0, "REPLACE")

        insert_at = modifier_insert_index([m.type for m in body.modifiers])
        stack_len_before = len(body.modifiers)

        min_dist, max_dist = proximity_distances(self.fit_distance, self.falloff)
        vwp = body.modifiers.new(names["vwp"], "VERTEX_WEIGHT_PROXIMITY")
        vwp.vertex_group = vertex_group.name
        vwp.target = corset
        vwp.proximity_mode = "GEOMETRY"
        vwp.proximity_geometry = {"FACE"}
        vwp.min_dist = min_dist
        vwp.max_dist = max_dist
        vwp.falloff_type = "SMOOTH"

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
            if insert_at < stack_len_before:
                for offset_index, mod_name in enumerate(
                    (names["vwp"], names["shrinkwrap"], names["smooth"])
                ):
                    bpy.ops.object.modifier_move_to_index(
                        modifier=mod_name, index=insert_at + offset_index
                    )
            if self.as_shapekey:
                self._bake_as_shapekey(context, body, names)
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
                "%s にコルセットフィット用モディファイアを構築しました" % body_name,
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

    def _bake_as_shapekey(self, context, body, names):
        """構築したモディファイアをシェイプキー Fit_<コルセット名> 1本に焼き込む(要: bodyがアクティブ)"""
        # VWP のウェイトをメッシュに焼く。表情シェイプキーを持つメッシュ(VRM素体など)では
        # modifier_apply が拒否されるため、評価済みオブジェクトからウェイトをコピーする
        try:
            bpy.ops.object.modifier_apply(modifier=names["vwp"])
        except RuntimeError:
            self._bake_proximity_weights(context, body, names)
            body.modifiers.remove(body.modifiers[names["vwp"]])

        # シェイプキーはモディファイアより先に評価されるため、Shrinkwrap のキーを 1.0 に
        # した状態で Smooth を焼くと「締め付け+スムーズ」の合成結果が最終キーに入る
        shrink_key = self._apply_as_shapekey(body, names["shrinkwrap"])
        shrink_key.value = 1.0
        final_key = self._apply_as_shapekey(body, names["smooth"])
        body.shape_key_remove(shrink_key)
        final_key.name = names["shapekey"]
        final_key.value = 1.0

    def _bake_proximity_weights(self, context, body, names):
        """評価済み(モディファイア適用後)の頂点ウェイトを元メッシュの頂点グループへコピーする"""
        depsgraph = context.evaluated_depsgraph_get()
        eval_body = body.evaluated_get(depsgraph)
        group_index = body.vertex_groups[names["vertex_group"]].index
        vertex_group = body.vertex_groups[names["vertex_group"]]
        for vertex in eval_body.data.vertices:
            weight = 0.0
            for element in vertex.groups:
                if element.group == group_index:
                    weight = element.weight
                    break
            vertex_group.add([vertex.index], weight, "REPLACE")

    def _apply_as_shapekey(self, body, modifier_name):
        """モディファイアをシェイプキーとして適用し、生成されたキーを返す"""
        bpy.ops.object.modifier_apply_as_shapekey(
            modifier=modifier_name, keep_modifier=False
        )
        return body.data.shape_keys.key_blocks[modifier_name]


_classes = (
    MYPLUGIN_OT_hello,
    MYPLUGIN_OT_add_cube_grid,
    MYPLUGIN_OT_fit_body_to_corset,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
