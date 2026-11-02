# SPDX-License-Identifier: GPL-3.0-or-later
"""摄像头回归：假网络/假设备，验证会话、凭据、云台及窗口生命周期。"""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from tests._isolate import isolate

isolate()

import requests
from PySide6.QtWidgets import QApplication, QDialog
from app.core.camera_bridge import (
    CameraBridge, CameraConnection, CameraError,
    RelayCameraSession,
)
from app.core.models import DeviceInfo
from app.core.service import MijiaService


def service():
    svc = object.__new__(MijiaService)
    svc._lock = threading.RLock()
    svc._api = MagicMock()
    return svc


def raises(fn, contains=""):
    try:
        fn()
    except CameraError as exc:
        assert contains in str(exc), str(exc)
    else:
        raise AssertionError("Expected a user-facing CameraError")


svc = service()
svc._api.get_devices_list.return_value = [{
    "did": "camera", "model": "test.camera.one", "isOnline": True, "localip": "192.168.1.7"}]
svc._api.get_shared_devices_list.return_value = []
svc._api.auth_data = {"userId": "test-user", "passToken": "DO-NOT-LOG"}
connection = svc.camera_connection("camera")
assert connection.ip == "192.168.1.7" and "DO-NOT-LOG" not in repr(connection)
assert "audio=0" in connection.source("sd")
svc._api.get_devices_list.return_value[0]["localip"] = "127.0.0.1"
raises(lambda: svc.camera_connection("camera"), "局域网")
print("1. Local connection validation and credential-safe representations OK")

svc = service()
svc._api.get_devices_list.return_value = [{
    "did": "camera", "model": "test.camera.one", "isOnline": True}]
svc._api.get_shared_devices_list.return_value = []
svc._api._request.return_value = {
    "vendor": {"vendor": 4, "vendor_params": {"p2p_id": "PRIVATE-UID", "init_string": "PRIVATE-INIT"}},
    "public_key": "11" * 32, "sign": "PRIVATE-SIGN"}
relay = svc.camera_relay_connection("camera")
assert "PRIVATE" not in repr(relay)
assert "audio=0" in relay.source("sd") and "relay_init=PRIVATE-INIT" in relay.source("sd")
assert len(relay.parameters["client_private"]) == 64
assert svc._api._request.call_args.args[0] == "/v2/device/miss_get_vendor"
assert "passToken" not in relay.parameters and "userId" not in relay.parameters
svc._api.set_devices_prop.assert_not_called()
svc._api.run_action.assert_not_called()
session = RelayCameraSession(svc, "camera")
session.close()
with patch("app.core.camera_bridge.subprocess.Popen") as popen:
    raises(lambda: session.start("sd"), "关闭")
    popen.assert_not_called()
late = RelayCameraSession(svc, "camera")
def authorize_after_close(did):
    late.close()
    return relay
with patch.object(svc, "camera_relay_connection", side_effect=authorize_after_close), \
        patch("app.core.camera_bridge.bridge_binary", return_value=Path("fake.exe")), \
        patch("app.core.camera_bridge.subprocess.Popen") as popen:
    raises(lambda: late.start("sd"), "关闭")
    popen.assert_not_called()
svc._api._request.return_value["vendor"]["vendor"] = 1
raises(lambda: svc.camera_relay_connection("camera"), "暂不支持")
print("1b. Relay provisioning excludes account credentials and never changes privacy state OK")

# 捕获实际写给子进程的配置；不启动真实网络或子进程。
saved = {}
process = MagicMock()
process.poll.return_value = None


def launch(args, **kwargs):
    saved["path"] = Path(args[-1])
    saved["config"] = json.loads(saved["path"].read_text(encoding="utf-8"))
    assert "DO-NOT-LOG" not in " ".join(args)
    return process


b = CameraBridge()
b._session = MagicMock()
b._session.auth = ("user", "random-local-auth")
b._session.get.return_value.ok = True
with patch("app.core.camera_bridge.bridge_binary", return_value=Path("fake.exe")), \
        patch("app.core.camera_bridge.subprocess.Popen", side_effect=launch), \
        patch("app.core.camera_process.CameraProcessJob") as job:
    assert b.start(connection).startswith("rtsp://127.0.0.1:")
    assert not saved["path"].exists(), "Credential file must be removed after startup"
    assert saved["config"]["api"]["listen"].startswith("127.0.0.1:")
    assert saved["config"]["rtsp"]["listen"].startswith("127.0.0.1:")
    assert saved["config"]["api"]["local_auth"] is True
    assert saved["config"]["webrtc"]["listen"] == ""
    b.close()
    job.return_value.close.assert_called_once()
    process.terminate.assert_called_once()

b = CameraBridge()
b.close()
with patch("app.core.camera_bridge.bridge_binary", return_value=Path("fake.exe")), \
        patch("app.core.camera_bridge.subprocess.Popen") as launch_mock:
    raises(lambda: b.start(connection), "关闭")
    launch_mock.assert_not_called()
print("2. Local-only configuration, temporary credential deletion and process cleanup OK")

b = CameraBridge()
b._api_url = "http://127.0.0.1:12345"
b._process = MagicMock()
b._process.poll.return_value = None
b._session = MagicMock()
for reply in ('{"ret":0}', '\x00\x00\x01\x13{"ret":0}',
              '\x00\x00\x01\x13{"ret":0,"angle":1,"elevation":0}\x00'):
    b._session.post.return_value.json.return_value = {"reply": reply}
    b.move("left")
    data = b._session.post.call_args.kwargs["params"]
    assert data["cmd"] == "0x112" and json.loads(data["data"]) == {"operation": 1}
for reply in ("", '{"ret":-1}', '{"ret":false}', '{}'):
    b._session.post.return_value.json.return_value = {"reply": reply}
    raises(lambda: b.move("right"))
raises(lambda: b.move("unknown"))
b._session.post.return_value.json.return_value = {
    "hex": (b'\x00\x00\x01\x13{"ret":0}\x00').hex()}
b.move("left")
b._session.post.return_value.json.return_value = {
    "hex": (b'\x00\x00\x01\x09{"ret":0}\x00').hex()}
raises(lambda: b.move("left"), "非云台")
b._session.post.return_value.json.return_value = {
    "hex": (b'\x00\x00\x01\x13{"ret":-2}\x00').hex()}
raises(lambda: b.move("right"), "ret=-2")
b._session.post.side_effect = requests.Timeout()
raises(lambda: b.move("up"), "确认")
print("3. PTZ small-step commands require device confirmation OK")

# UI 的开始/停止/过期回调、出画面前禁用云台，不创建真实播放器或视频流。
from app.ui.camera_dialog import CameraDialog


class Jobs:
    def __init__(self):
        self.tasks = []
    def submit(self, fn, on_success=None, on_error=None):
        self.tasks.append((fn, on_success, on_error))
    def shutdown(self):
        pass
    def finish(self):
        fn, ok, error = self.tasks.pop(0)
        try:
            result = fn()
        except Exception as exc:
            error(exc)
        else:
            ok(result)


app = QApplication.instance() or QApplication([])
device = DeviceInfo("camera", "Test camera", "test.camera.one", "", "", True)
for mode in ("cloud", "lan"):
    with patch("app.ui.camera_dialog.QMediaPlayer") as player_class, \
            patch("app.ui.camera_dialog.CameraBridge") as bridge_class:
        bridge_class.return_value.start.return_value = "rtsp://127.0.0.1:1/camera"
        # 远程与局域网都必须先收到有效视频帧才能操作云台。
        class FakeRelay:
            def start(self, quality): return "rtsp://127.0.0.1:1/camera"
            def close(self): pass
        with patch("app.ui.camera_dialog.RelayCameraSession", FakeRelay):
            FakeRelay.__init__ = lambda self, *args: None
            dialog = CameraDialog(MagicMock(), device)
            dialog._jobs.shutdown()
            dialog._jobs = Jobs()
            dialog._mode.setCurrentIndex(dialog._mode.findData(mode))
            dialog.start_preview()
            dialog._jobs.finish()
            assert not any(b.isEnabled() for b in dialog._directions.values())
            dialog._frame_received(SimpleNamespace(isValid=lambda: True))
            assert all(b.isEnabled() for b in dialog._directions.values())
            old_generation = dialog._generation
            dialog.stop_preview()
            assert dialog._start_btn.isEnabled() and not dialog._stop_btn.isEnabled()
            before = player_class.return_value.play.call_count
            dialog._connected(old_generation, "rtsp://127.0.0.1:1/camera")
            assert player_class.return_value.play.call_count == before
            dialog._connect_error(dialog._generation, CameraError("视频地址返回 HTTP 404"))
            assert "404" in dialog._status.text()
            assert "连接失败" in dialog._placeholder.text()
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
print("4. Preview controls, stale callbacks and PTZ frame gating OK")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
app.setQuitOnLastWindowClosed(False)
with patch("app.ui.camera_dialog.QMediaPlayer"):
    dialog = CameraDialog(MagicMock(), device)
    dialog._jobs.shutdown()
    dialog._jobs = Jobs()
    bridge = MagicMock()
    dialog._bridge = bridge
    dialog._has_frame = True
    dialog._set_directions(True)
    dialog.show()
    app.processEvents()
    button = dialog._directions["right"]
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    assert len(dialog._jobs.tasks) == 1 and button.isEnabled()
    QTest.qWait(900)
    assert len(dialog._jobs.tasks) == 1, "Slow commands must not pile up"
    dialog._jobs.finish()
    QTest.qWait(400)
    assert not dialog._jobs.tasks, "Repeats must be low frequency"
    QTest.qWait(500)
    assert len(dialog._jobs.tasks) == 1
    dialog._jobs.finish()
    QTest.qWait(900)
    assert len(dialog._jobs.tasks) == 1
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    dialog._jobs.finish()  # 已排队的长按重复在松开后也不能再执行。
    QTest.qWait(900)
    assert not dialog._jobs.tasks and bridge.move.call_count == 2
    assert not dialog._hold_timer.isActive()

    for event_type in (QEvent.Type.Leave, QEvent.Type.WindowDeactivate):
        button.setDown(True)
        dialog._press_direction("right")
        dialog._jobs.finish()
        target = button if event_type == QEvent.Type.Leave else dialog
        QCoreApplication.sendEvent(target, QEvent(event_type))
        assert dialog._held_direction is None and not dialog._hold_timer.isActive()
        button.setDown(False)

    bridge.move.side_effect = CameraError("ret=-2")
    button.setDown(True)
    dialog._press_direction("right")
    dialog._jobs.finish()
    assert dialog._held_direction is None and not dialog._hold_timer.isActive()
    assert "ret=-2" in dialog._status.text()
    bridge.move.side_effect = None
    bridge.move.reset_mock()
    dialog._press_direction("right")
    dialog.stop_preview()
    dialog._jobs.finish()
    bridge.move.assert_not_called()
    assert not dialog._hold_timer.isActive()
    dialog.reject()
print("5. Hold-to-repeat throttling, release, focus loss, errors and queued-stop cancellation OK")
print("CAMERA REGRESSION ALL PASS")
