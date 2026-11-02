# SPDX-License-Identifier: GPL-3.0-or-later
"""摄像头入口卡片：点击才创建弹窗，弹窗验证真实开关及清理。"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from tests._isolate import isolate
isolate()

from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from app.core.models import DeviceInfo, DeviceDetail, PropInfo
from app.ui.camera_preview_card import CameraPreviewCard
from app.ui.camera_dialog import PowerAwareCameraDialog
from app.ui.workbench_panel import WorkbenchPanel


class Jobs:
    def __init__(self): self.tasks = []
    def submit(self, fn, on_success=None, on_error=None):
        self.tasks.append((fn, on_success, on_error))
    def shutdown(self): pass
    def finish(self):
        fn, success, error = self.tasks.pop(0)
        try: value = fn()
        except Exception as exc:
            if error: error(exc)
        else:
            if success: success(value)


app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
device = DeviceInfo("camera-test", "测试摄像头", "test.camera.one", "", "", True)
svc = MagicMock()
svc.power_state.return_value = False
root = QWidget()
layout = QVBoxLayout(root)
with patch("app.ui.camera_dialog.QMediaPlayer"), \
        patch("app.ui.camera_dialog.CameraDialog.start_preview") as start:
    card = PowerAwareCameraDialog(svc, device, root)
    card._jobs.shutdown()
    card._jobs = Jobs()
    root.show()
    card.show()
    app.processEvents()
    assert card.isWindow()
    card._jobs.finish()
    assert "关闭" in card._placeholder.text() and not card._start_btn.isEnabled()
    start.assert_not_called()
    svc.power_state.return_value = None
    card.refresh_power(); card._jobs.finish()
    assert "无法确认" in card._placeholder.text()
    start.assert_not_called()
    svc.power_state.return_value = True
    card.refresh_power(); card._jobs.finish()
    start.assert_called_once()
    assert "已开启" in card._power_label.text()
    card.refresh_power(); card._jobs.finish()
    assert start.call_count == 1, "Polling must not repeatedly reconnect"
    bridge = card._bridge = MagicMock()
    card._has_frame = True
    card._apply_power(False)
    bridge.close.assert_called_once()
    assert card._bridge is None and not any(b.isEnabled() for b in card._directions.values())
    card.stop_preview()
    card._apply_power(True)
    assert start.call_count == 1, "Manual pause must survive state polling"
    card.start_preview(); card._jobs.finish()
    assert start.call_count == 2
    # 旧的开启回包不得覆盖正在进行的关闭写入。
    card.refresh_power()
    card.begin_power_off()
    card._jobs.finish()
    assert card._power_state is False and start.call_count == 2
    card.refresh_power()
    assert not card._jobs.tasks
    svc.power_state.return_value = False
    card.power_write_finished(); card._jobs.finish()
    assert card._power_state is False
    card.refresh_power()
    card.hide()
    svc.power_state.return_value = True
    card._jobs.finish()
    assert not card._power_timer.isActive() and start.call_count == 2
    card.dispose()
svc.set_power_state.assert_not_called()
svc.write_prop.assert_not_called()
print("1. Popup power gating, autoplay, pause and stale power reads OK")

with patch("app.ui.camera_dialog.QMediaPlayer") as player, \
        patch("app.ui.camera_dialog.PowerAwareCameraDialog") as popup:
    entry = CameraPreviewCard(svc, device, root)
    entry._jobs.shutdown()
    entry._jobs = Jobs()
    layout.addWidget(entry)
    root.show(); entry.show(); app.processEvents()
    svc.power_state.return_value = True
    entry._jobs.finish()
    assert entry._button.isEnabled() and "已开启" in entry._subtitle.text()
    assert entry.height() == 88 and entry._dialog is None
    popup.assert_not_called()
    player.assert_not_called()
    entry._button.click()
    popup.assert_called_once()
    popup.return_value.exec.assert_called_once()
    popup.return_value.dispose.assert_called_once()
    assert entry._dialog is None
    entry._apply_power(False)
    assert not entry._button.isEnabled() and "已关闭" in entry._subtitle.text()
    entry._button.click()
    assert popup.call_count == 1
    entry.dispose(); entry.hide()
print("1b. Compact card creates no player or popup before click; closed devices cannot open OK")


class FakeCard(QWidget):
    def __init__(self, *args):
        super().__init__(args[-1])
        self.dispose = MagicMock()
        self.retheme = MagicMock()
        self.begin_power_off = MagicMock()
        self.power_write_finished = MagicMock()


with patch("app.ui.camera_preview_card.CameraPreviewCard", FakeCard):
    panel = WorkbenchPanel(svc, Jobs())
    panel.show_device(device.did, device=device)
    first = panel._camera_preview
    panel._on_detail_loaded(DeviceDetail(device.did, device.name, device.model, [], []))
    assert panel._left_col.itemAt(0).widget() is first
    assert not panel._body.isHidden(), "Camera preview must work without user-selected modules"
    panel.retheme()
    assert panel._camera_preview is first
    first.dispose.assert_not_called()
    primary = PropInfo("on-2", "摄像头开关", "bool", True, True)
    indicator = PropInfo("on-3", "指示灯", "bool", True, True)
    panel._detail.props = [primary, indicator]
    panel._jobs.tasks.clear()
    panel._write(indicator, indicator.name, False)
    first.begin_power_off.assert_not_called()
    panel._jobs.finish()
    panel._write(primary, primary.name, False)
    first.begin_power_off.assert_called_once()
    panel._jobs.finish()
    assert first.power_write_finished.call_count == 2
    other = DeviceInfo("plug", "插座", "test.plug.one", "", "", True)
    panel.show_device(other.did, device=other)
    first.dispose.assert_called_once()
    assert panel._camera_preview is None
    panel.deleteLater()
print("2. Camera preview is first, persists across rendering, and disposes on device switch OK")

from app.ui.device_dialog import DeviceDetailDialog
with patch("app.ui.camera_preview_card.CameraPreviewCard", FakeCard):
    dialog = DeviceDetailDialog(svc, Jobs(), device)
    dialog.panel.show_device(device.did, device=device)
    preview = dialog.panel._camera_preview
    dialog.setWindowOpacity(0)
    dialog.reject()
    preview.dispose.assert_called_once()
    dialog.deleteLater()
print("3. Main-switch off gating and parent detail close cleanup OK")
print("CAMERA CARD REGRESSION ALL PASS")
