"""costume/async_bridge.py のテスト。

実物の `claude -p` は呼ばない。worker を注入して、スレッド境界の契約
(実行中は None / 完了で result / 例外は error として運ばれる)だけを検査する。
"""

import threading

import pytest

from my_blender_plugin.costume import ai_bridge, async_bridge


def test_poll_is_none_while_running_then_returns_result():
    gate = threading.Event()

    def worker(text, runner=None, timeout=None):
        assert gate.wait(5), "テストが gate.set() を呼んでいない"
        return {"source": "ai", "spec": {"name": text}}

    job = async_bridge.AsyncSpecJob("紺のミニスカート", worker=worker).start()
    assert job.poll() is None
    assert job.running()

    gate.set()
    assert job.wait(5), "worker が5秒で完了しなかった"
    outcome = job.poll()
    assert outcome == {"result": {"source": "ai", "spec": {"name": "紺のミニスカート"}}}
    assert not job.running()
    # poll は何度呼んでも同じ結果を返す(タイマーから繰り返し呼ばれる)
    assert job.poll() == outcome


def test_worker_exception_is_carried_to_the_main_thread():
    def worker(text, runner=None, timeout=None):
        raise ValueError("boom")

    job = async_bridge.AsyncSpecJob("x", worker=worker).start()
    assert job.wait(5)
    outcome = job.poll()
    assert isinstance(outcome["error"], ValueError)
    assert "result" not in outcome


def test_text_runner_timeout_are_passed_to_the_worker():
    seen = {}
    sentinel = object()

    def worker(text, runner=None, timeout=None):
        seen.update(text=text, runner=runner, timeout=timeout)
        return "ok"

    job = async_bridge.AsyncSpecJob("布", runner=sentinel, timeout=12, worker=worker).start()
    assert job.wait(5)
    assert job.poll() == {"result": "ok"}
    assert seen == {"text": "布", "runner": sentinel, "timeout": 12}


def test_default_worker_and_timeout_are_the_ai_bridge_ones():
    job = async_bridge.AsyncSpecJob("x")
    assert job._work is ai_bridge.spec_from_text
    assert job._timeout == ai_bridge.DEFAULT_TIMEOUT


def test_thread_is_daemon_so_blender_can_quit():
    gate = threading.Event()

    def worker(text, runner=None, timeout=None):
        gate.wait(5)
        return None

    job = async_bridge.AsyncSpecJob("x", worker=worker).start()
    try:
        assert job._thread.daemon
    finally:
        gate.set()
        job.wait(5)


def test_poll_before_start_raises():
    job = async_bridge.AsyncSpecJob("x", worker=lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        job.poll()


def test_start_twice_raises():
    job = async_bridge.AsyncSpecJob("x", worker=lambda text, runner=None, timeout=None: None)
    job.start()
    job.wait(5)
    with pytest.raises(RuntimeError):
        job.start()


def test_operator_delegates_ai_path_to_the_modal_operator():
    """生成オペレーターは AI 使用時にモーダル版へ引き継ぐ(同期で待たない)"""
    from my_blender_plugin import operators

    assert operators.MYPLUGIN_OT_generate_costume_ai in operators._classes
    assert (
        operators.MYPLUGIN_OT_generate_costume_ai.bl_idname
        == "myplugin.generate_costume_ai"
    )
    # F3 検索に二重に出さない
    assert "INTERNAL" in operators.MYPLUGIN_OT_generate_costume_ai.bl_options
