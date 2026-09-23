"""把"整扇窗"真的开起来跑一遍：菜单、动作、托盘、对话框、心跳。

真正干活的是 `tests/gui_smoke.py`（子进程 + 一次性 HOME + 独立 APP_ID），
这里只负责给它一个自己的显示器和超时。**绝不用当前会话的 DISPLAY**——
那会把窗口弹到用户眼前，还可能碰到他真正的配置与壁纸。

没有 Xvfb 就跳过（而不是失败）：CI 里装了 xvfb，本地 `apt install xvfb` 即可。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "gui_smoke.py"


def _free_display() -> int:
    """挑一个没人用的显示号（避开 0 号：那是用户自己的会话）。"""
    for n in range(90, 120):
        if not Path(f"/tmp/.X{n}-lock").exists():
            return n
    return 130


class TestGuiSmoke(unittest.TestCase):

    @unittest.skipUnless(shutil.which("Xvfb"), "没有 Xvfb，跳过 GUI 冒烟")
    def test_window_opens_and_actions_work(self):
        display = _free_display()
        runtime = tempfile.mkdtemp(prefix="chuang-xvfb-")
        os.chmod(runtime, 0o700)
        xvfb = subprocess.Popen(
            ["Xvfb", f":{display}", "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            for _ in range(40):          # 等它起来
                if Path(f"/tmp/.X{display}-lock").exists():
                    break
                time.sleep(0.1)
            env = dict(os.environ, DISPLAY=f":{display}", XDG_RUNTIME_DIR=runtime)
            try:
                r = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, env=env,
                                   capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                self.fail("GUI 冒烟超时（>=180 秒）——多半是窗口没退出来")
            detail = f"\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr[-2000:]}"
            self.assertEqual(r.returncode, 0, f"GUI 冒烟失败{detail}")
            self.assertIn("problems", r.stdout, detail)
            payload = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertEqual(payload["problems"], [], detail)
            # 顺手确认几件关键的事真的被验过（探针没跑完的话这些键会缺）
            for key in ("menu_actions", "toggles", "activated", "dialogs",
                        "tray_activate", "city_change"):
                self.assertIn(key, payload, f"探针没跑到 {key}{detail}")
            self.assertEqual(payload["tray_activate"], "ok", detail)
            self.assertEqual(payload["tick_errors_after"], 0, detail)
        finally:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
            shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
