# SPDX-License-Identifier: GPL-3.0-or-later
"""摄像头首张入口卡片；只有点击弹窗后才创建播放器和连接视频。"""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
import qtawesome as qta

from app.core.jobs import JobExecutor
from app.ui.si_theme import SiColors


class CameraPreviewCard(QFrame):
    def __init__(self, service, device, parent=None):
        super().__init__(parent)
        self._service, self._device = service, device
        self._jobs = JobExecutor(self)
        self._dialog = None
        self._disposed = False
        self._power_state = None
        self._power_pending = False
        self._power_generation = 0
        self._power_write_pending = False
        self.setFixedHeight(88)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._button = QPushButton()
        self._button.setAccessibleName("实时预览")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setMinimumHeight(86)
        self._button.clicked.connect(self.open_preview)
        content = QHBoxLayout(self._button)
        content.setContentsMargins(20, 12, 20, 12)
        content.setSpacing(18)
        self._icon = QLabel()
        self._icon.setFixedSize(40, 40)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self._icon)
        text = QVBoxLayout()
        text.setSpacing(5)
        self._title = QLabel("实时预览")
        self._subtitle = QLabel()
        text.addWidget(self._title)
        text.addWidget(self._subtitle)
        content.addLayout(text, 1)
        self._arrow = QLabel()
        self._arrow.setFixedSize(20, 24)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self._arrow)
        for label in (self._icon, self._title, self._subtitle, self._arrow):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._button)
        self._power_timer = QTimer(self)
        self._power_timer.setInterval(5000)
        self._power_timer.timeout.connect(self.refresh_power)
        self.retheme()
        self._apply_power(None)

    def retheme(self):
        self._button.setStyleSheet(
            f"QPushButton {{ background: {SiColors.CARD}; color: {SiColors.TEXT_PRIMARY};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 14px;"
            " padding: 0; }"
            f"QPushButton:hover {{ background: {SiColors.CARD_HOVER}; border-color: {SiColors.CARD_BORDER_HOVER}; }}"
            f"QPushButton:disabled {{ color: {SiColors.TEXT_SECONDARY}; }}")
        self._title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent; border: none;"
            " font-size: 11pt; font-weight: 600;")
        self._subtitle.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; border: none;"
            " font-size: 9pt; font-weight: 400;")
        self._arrow.setStyleSheet("background: transparent; border: none;")
        self._arrow.setPixmap(qta.icon("mdi.chevron-right", color=SiColors.ICON_DIM).pixmap(20, 20))
        self._update_icon()

    def _update_icon(self):
        active = self._device.online and self._power_state is True
        background = SiColors.THEME if active else SiColors.SURFACE
        foreground = SiColors.WHITE if active else SiColors.ICON_DIM
        self._icon.setStyleSheet(
            f"background: {background}; border: none; border-radius: 20px;")
        self._icon.setPixmap(qta.icon("mdi.video-outline", color=foreground).pixmap(24, 24))

    def refresh_power(self):
        if (self._disposed or self._power_pending or self._power_write_pending
                or not self.isVisible()):
            return
        self._power_pending = True
        generation = self._power_generation

        def apply(state):
            if self._disposed or generation != self._power_generation:
                return
            self._power_pending = False
            self._apply_power(state)

        self._jobs.submit(lambda: self._service.power_state(self._device.did)
                          if self._device.online else None,
                          on_success=apply, on_error=lambda _: apply(None))

    def _apply_power(self, state):
        self._power_state = state if type(state) is bool else None
        if not self._device.online:
            text = "已离线"
        elif self._power_state is True:
            text = "已开启 · 点击查看画面"
        elif self._power_state is False:
            text = "已关闭 · 开启后可预览"
        else:
            text = "开关状态未知 · 请刷新"
        self._subtitle.setText(text)
        self._button.setAccessibleDescription(text)
        self._update_icon()
        self._button.setEnabled(self._device.online and self._power_state is True)
        if self._dialog is not None and self._power_state is not True:
            self._dialog._apply_power(self._power_state)

    def open_preview(self):
        if self._disposed or self._power_state is not True or not self._device.online:
            return
        if self._dialog is not None:
            self._dialog.raise_()
            return
        from app.ui.camera_dialog import PowerAwareCameraDialog
        self._power_timer.stop()
        dialog = self._dialog = PowerAwareCameraDialog(self._service, self._device, self.window())
        try:
            dialog.exec()
        finally:
            dialog.dispose()
            dialog.deleteLater()
            self._dialog = None
            if not self._disposed and self.isVisible():
                self._power_timer.start()
                self.refresh_power()

    def begin_power_off(self):
        self._power_write_pending = True
        self._power_generation += 1
        self._power_pending = False
        self._apply_power(False)

    def power_write_finished(self):
        self._power_write_pending = False
        self.refresh_power()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._disposed:
            self._power_timer.start()
            self.refresh_power()

    def hideEvent(self, event):
        self._power_timer.stop()
        self._power_generation += 1
        self._power_pending = False
        if self._dialog is not None:
            self._dialog.reject()
        super().hideEvent(event)

    def dispose(self):
        self._disposed = True
        self._power_generation += 1
        self._power_timer.stop()
        if self._dialog is not None:
            self._dialog.reject()
        self._jobs.shutdown()
