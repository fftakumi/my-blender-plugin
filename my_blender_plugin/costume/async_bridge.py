"""`spec_from_text` をバックグラウンドスレッドで走らせる橋。bpy 非依存。

`claude -p` の応答(実測1分前後、作り直しが入ると2回分)を UI スレッドで
同期待ちすると Blender のウィンドウが固まる。ここは**待ちだけ**を別スレッドへ
逃がす。bpy はスレッドセーフではないので、スレッドが実行するのは bpy 非依存の
`ai_bridge.spec_from_text` だけに限定し、結果はメインスレッド(オペレーターの
modal)が `poll()` で引き取ってからメッシュ生成に進む。

キャンセルは「結果を捨てる」ことしかしない。走り出した subprocess は
`ai_bridge.DEFAULT_TIMEOUT` が上限を張るので、放置しても自然に終わる。
"""

import threading

from . import ai_bridge


class AsyncSpecJob:
    """`spec_from_text` 1回分を別スレッドで実行する使い捨てジョブ。

    使い方: ``job = AsyncSpecJob(text).start()`` して、タイマーごとに
    ``job.poll()``。実行中は None、完了したら ``{"result": ...}`` か
    ``{"error": 例外}`` を返す(スレッド内の例外はここへ運ばれる)。

    ``worker`` はテスト注入用で、既定は `ai_bridge.spec_from_text`。
    """

    def __init__(self, text, runner=None, timeout=ai_bridge.DEFAULT_TIMEOUT, worker=None):
        self._work = worker or ai_bridge.spec_from_text
        self._text = text
        self._runner = runner
        self._timeout = timeout
        self._outcome = None
        self._done = threading.Event()
        self._thread = None

    def start(self):
        """スレッドを起動して self を返す(1回だけ呼べる)"""
        if self._thread is not None:
            raise RuntimeError("AsyncSpecJob は使い捨てです(start は1回だけ)")
        # daemon: Blender 終了時にこのスレッドが終了を妨げないようにする
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="costume-ai-spec"
        )
        self._thread.start()
        return self

    def _run(self):
        try:
            self._outcome = {
                "result": self._work(self._text, runner=self._runner, timeout=self._timeout)
            }
        except BaseException as error:  # スレッド内で握り潰さずメインへ運ぶ
            self._outcome = {"error": error}
        finally:
            self._done.set()

    def running(self):
        return self._thread is not None and not self._done.is_set()

    def poll(self):
        """実行中は None。完了したら {"result": ...} または {"error": 例外}"""
        if self._thread is None:
            raise RuntimeError("start() の前に poll() が呼ばれました")
        if not self._done.is_set():
            return None
        return self._outcome

    def wait(self, timeout=None):
        """完了を待つ(テスト用。UI スレッドから呼ぶと本末転倒なので呼ばない)"""
        return self._done.wait(timeout)
