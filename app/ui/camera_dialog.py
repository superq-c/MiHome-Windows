# SPDX-License-Identifier: GPL-3.0-or-later
"""摄像头实时视频窗口，关闭或停止预览时断开视频会话。"""

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedLayout, QVBoxLayout,
)

from app.core.camera_bridge import CameraBridge, CameraError, RelayCameraSession
from app.core.jobs import JobExecutor
from app.ui.si_theme import SiColors


class CameraDialog(QDialog):
    def __init__(self, service, device, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"实时视频 · {device.name}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(960, 680)
        self._service, self._device = service, device
        self._jobs = JobExecutor(self)
        self._bridge = None
        self._generation = 0
        self._moving = False
        self._has_frame = False
        self._disposed = False
        self._held_direction = None
        self._hold_generation = 0
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(800)
        self._hold_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._hold_timer.timeout.connect(self._repeat_move)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(device.name))
        row.addStretch()
        self._mode = QComboBox()
        self._mode.addItem("远程预览", "cloud")
        self._mode.addItem("局域网预览", "lan")
        row.addWidget(self._mode)
        self._quality = QComboBox()
        self._quality.addItem("高清", "hd")
        self._quality.addItem("流畅", "sd")
        self._quality.setToolTip("清晰度在下次开始预览时生效")
        row.addWidget(self._quality)
        self._start_btn = QPushButton("开始预览")
        self._start_btn.clicked.connect(self.start_preview)
        self._start_btn.setEnabled(device.online)
        row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("停止预览")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_preview)
        row.addWidget(self._stop_btn)
        layout.addLayout(row)
        self._power_label = QLabel()
        self._power_label.hide()
        layout.addWidget(self._power_label)

        self._video = QVideoWidget()
        self._video.setMinimumSize(480, 270)
        self._video.setStyleSheet("background: #111111;")
        self._display = QStackedLayout()
        self._display.addWidget(self._video)
        self._placeholder = QLabel("点击“开始预览”连接摄像头")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setMinimumSize(480, 270)
        self._placeholder.setStyleSheet("background: #111111; color: #aaaaaa; border-radius: 8px;")
        self._display.addWidget(self._placeholder)
        self._display.setCurrentWidget(self._placeholder)
        layout.addLayout(self._display, 1)
        self._status = QLabel("点击开始预览；远程模式通过摄像头授权的中继连接。" if device.online
                              else "摄像头已离线，请上线后重新打开。")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("云台（点击 / 长按）"))
        grid = QGridLayout()
        self._directions = {}
        for direction, label, r, c in (("up", "↑", 0, 1), ("left", "←", 1, 0),
                                       ("right", "→", 1, 2), ("down", "↓", 2, 1)):
            button = QPushButton(label)
            button.setFixedSize(42, 32)
            button.setEnabled(False)
            button.setToolTip({"up": "向上", "down": "向下", "left": "向左", "right": "向右"}[direction])
            button.pressed.connect(lambda d=direction: self._press_direction(d))
            button.released.connect(lambda d=direction: self._release_direction(d))
            button.installEventFilter(self)
            grid.addWidget(button, r, c)
            self._directions[direction] = button
        controls.addLayout(grid)
        hint = QLabel("点击转动一步；长按低频连续转动，松开停止。\n预览不录制、不播放声音；方向与步长可能因型号而异。")
        hint.setWordWrap(True)
        controls.addWidget(hint, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        controls.addWidget(close)
        layout.addLayout(controls)

        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self._video)
        self._player.errorOccurred.connect(self._media_error)
        self._video.videoSink().videoFrameChanged.connect(self._frame_received)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(120_000)
        self._timeout.timeout.connect(self._connection_timeout)
        QApplication.instance().aboutToQuit.connect(self.stop_preview)

    def start_preview(self) -> None:
        if self._bridge is not None or self._disposed:
            return
        self._generation += 1
        generation = self._generation
        cloud = self._mode.currentData() == "cloud"
        bridge = self._bridge = (RelayCameraSession(self._service, self._device.did)
                                if cloud else CameraBridge())
        quality = self._quality.currentData()
        self._has_frame = False
        self._start_btn.setEnabled(False)
        self._quality.setEnabled(False)
        self._mode.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status.setText("正在连接摄像头…")
        self._placeholder.setText("正在连接摄像头…")
        self._display.setCurrentWidget(self._placeholder)
        self._timeout.start()

        def connect():
            try:
                if cloud:
                    return bridge.start(quality)
                return bridge.start(self._service.camera_connection(self._device.did), quality)
            except Exception:
                bridge.close()
                raise

        self._jobs.submit(connect,
                          on_success=lambda url: self._connected(generation, url),
                          on_error=lambda exc: self._connect_error(generation, exc))

    def _connected(self, generation, url):
        if generation != self._generation or self._bridge is None:
            return
        self._player.setSource(QUrl(url))
        self._player.play()

    def _connect_error(self, generation, exc):
        if generation != self._generation:
            return
        self.stop_preview()
        self._status.setText(str(exc) if isinstance(exc, CameraError)
                             else "视频连接失败，请检查摄像头与电脑网络后重试。")
        self._placeholder.setText("连接失败，尚未收到视频画面")

    def _frame_received(self, frame):
        if self._bridge is None or not frame.isValid():
            return
        self._timeout.stop()
        if not self._has_frame:
            self._has_frame = True
            self._display.setCurrentWidget(self._video)
            remote = isinstance(self._bridge, RelayCameraSession)
            self._status.setText("远程预览中 · 视频仅在本机播放" if remote
                                 else "实时预览中 · 视频仅在本机播放")
            self._set_directions(True)

    def _set_directions(self, enabled):
        for button in self._directions.values():
            button.setEnabled(enabled)

    def _press_direction(self, direction):
        if self._bridge is None or not self._has_frame:
            return
        self._hold_timer.stop()
        self._hold_generation += 1
        self._held_direction = direction
        self._move(direction)

    def _release_direction(self, direction=None):
        if direction is None or self._held_direction == direction:
            self._hold_generation += 1
            self._held_direction = None
            self._hold_timer.stop()

    def _repeat_move(self):
        direction = self._held_direction
        if direction and self._directions[direction].isDown():
            self._move(direction, repeat=True)
        else:
            self._release_direction()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
            for direction, button in getattr(self, "_directions", {}).items():
                if watched is button:
                    self._release_direction(direction)
                    break
        return super().eventFilter(watched, event)

    def event(self, event):
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            if hasattr(self, "_hold_timer"):
                self._release_direction()
        return super().event(event)

    def _media_error(self, *_):
        if self._bridge is None:
            return
        self.stop_preview()
        self._status.setText("无法播放视频，可切换“流畅”后重试；局域网模式需与摄像头在同一网络。")
        self._placeholder.setText("视频播放失败")

    def _connection_timeout(self):
        self.stop_preview()
        self._status.setText("等待视频超时，请检查摄像头网络或切换“流畅”后重试。")
        self._placeholder.setText("连接超时，尚未收到视频画面")

    def _move(self, direction, repeat=False):
        if self._bridge is None or not self._has_frame or self._moving:
            return
        self._moving = True
        generation, bridge = self._generation, self._bridge
        hold_generation = self._hold_generation

        def send():
            # 停止/关闭后不执行仍在队列中等待的电机指令。
            held = (self._held_direction == direction
                    and hold_generation == self._hold_generation)
            if (generation == self._generation and self._bridge is bridge
                    and (not repeat or held)):
                bridge.move(direction)
                return True
            return False

        self._jobs.submit(send,
                          on_success=lambda sent: self._move_done(generation, skipped=not sent),
                          on_error=lambda exc: self._move_done(generation, exc))

    def _move_done(self, generation, exc=None, skipped=False):
        if generation != self._generation:
            return
        self._moving = False
        self._set_directions(self._has_frame)
        if exc:
            self._release_direction()
        elif self._held_direction and self._has_frame:
            # 上一步确认后再等 800ms；最多一条指令在途，慢网络不会积压。
            self._hold_timer.start()
        if skipped:
            return
        self._status.setText((str(exc) if isinstance(exc, CameraError)
                              else "云台控制失败，请重试。") if exc
                             else "云台指令已确认 · 实时预览中")

    def stop_preview(self):
        self._generation += 1  # 忽略停止/关闭后到达的连接回调
        self._release_direction()
        self._timeout.stop()
        bridge, self._bridge = self._bridge, None
        self._has_frame = self._moving = False
        self._set_directions(False)
        self._player.stop()
        self._player.setSource(QUrl())
        if bridge is not None:
            bridge.close()
        self._start_btn.setEnabled(self._device.online)
        self._quality.setEnabled(True)
        self._mode.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status.setText("预览已停止")
        self._placeholder.setText("预览已停止")
        self._display.setCurrentWidget(self._placeholder)

    def done(self, result):
        self.dispose()
        super().done(result)

    def dispose(self):
        self.stop_preview()
        self._disposed = True
        self._jobs.shutdown()


class PowerAwareCameraDialog(CameraDialog):
    """每次预览前确认真实主开关，窗口可见时持续跟踪设备状态。"""

    def __init__(self, service, device, parent=None):
        super().__init__(service, device, parent)
        self._power_state = None
        self._power_pending = False
        self._power_generation = 0
        self._power_write_pending = False
        self._preview_wanted = True
        self._attempted = False
        self._power_timer = QTimer(self)
        self._power_timer.setInterval(5000)
        self._power_timer.timeout.connect(self.refresh_power)
        self._power_label.setText("摄像头状态：读取中")
        self._power_label.show()
        self._placeholder.setText("正在确认摄像头开关状态…")
        self._start_btn.setEnabled(False)
        self.retheme()

    def retheme(self):
        self._power_label.setStyleSheet(f"color: {SiColors.TEXT_SECONDARY};")

    def refresh_power(self):
        if (self._disposed or self._power_pending or self._power_write_pending
                or not self.isVisible()):
            return
        self._power_pending = True
        generation = self._power_generation

        def apply(state):
            if generation != self._power_generation or self._disposed:
                return
            self._power_pending = False
            self._apply_power(state)

        self._jobs.submit(lambda: self._service.power_state(self._device.did)
                          if self._device.online else None,
                          on_success=apply, on_error=lambda _: apply(None))

    def begin_power_off(self):
        self._power_write_pending = True
        self._power_generation += 1
        self._power_pending = False
        self._apply_power(False)

    def power_write_finished(self):
        self._power_write_pending = False
        self.refresh_power()

    def _apply_power(self, state):
        self._power_state = state if type(state) is bool else None
        if self._power_state is not True or not self._device.online:
            CameraDialog.stop_preview(self)
            self._attempted = False
            self._start_btn.setEnabled(False)
            if not self._device.online:
                text = "摄像头已离线"
            elif self._power_state is False:
                text = "摄像头已关闭"
            else:
                text = "暂时无法确认摄像头开关状态"
            self._power_label.setText(f"设备状态：{text}")
            self._placeholder.setText(text)
            self._status.setText("在米家或设备详情页开启设备后可预览。"
                                 if self._power_state is False else "请检查设备网络后刷新。")
            return
        self._power_label.setText("设备状态：已开启")
        self._start_btn.setEnabled(self._bridge is None)
        if self._preview_wanted and not self._attempted and self.isVisible():
            self._attempted = True
            CameraDialog.start_preview(self)

    def start_preview(self):
        # 点击开始也必须重新读取开关，不能依赖数秒前的缓存。
        self._preview_wanted = True
        self._attempted = False
        self.refresh_power()

    def stop_preview(self):
        self._preview_wanted = False
        super().stop_preview()
        self._start_btn.setEnabled(self._power_state is True and self._device.online)

    def _move(self, direction, repeat=False):
        if self._power_state is True:
            super()._move(direction, repeat=repeat)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._disposed:
            self._power_timer.start()
            self.refresh_power()

    def hideEvent(self, event):
        if not hasattr(self, "_power_timer"):
            super().hideEvent(event)
            return
        self._power_timer.stop()
        self._power_generation += 1
        self._power_pending = False
        CameraDialog.stop_preview(self)
        self._attempted = False
        super().hideEvent(event)

    def dispose(self):
        self._power_timer.stop()
        self._power_generation += 1
        super().dispose()
