import my_blender_plugin


def test_bl_info():
    info = my_blender_plugin.bl_info
    assert info["name"]
    assert isinstance(info["version"], tuple)


def test_register_unregister():
    # フェイク bpy 上で register/unregister が例外なく通ることを確認する
    my_blender_plugin.register()
    my_blender_plugin.unregister()
