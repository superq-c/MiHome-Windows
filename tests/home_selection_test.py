# SPDX-License-Identifier: GPL-3.0-or-later
"""分组选择持久化、首组选中及旧“全部”入口移除回归。"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from tests._isolate import isolate
isolate()

from unittest.mock import MagicMock, patch
from app.core import settings_store
from app.core.models import DeviceInfo
from app.ui.main_window import MainWindow


def devices(*homes):
    return [DeviceInfo(str(i), "测试设备", "test.plug.one", home, "房间", True)
            for i, home in enumerate(homes)]


def window(current=""):
    probe = MagicMock()
    probe._current_home = current
    probe._room_initialized = True
    probe._current_room = "全屋"
    probe._natural_tabs.return_value = ["全屋"]
    probe._show_scene_tab = False
    return probe


settings_store.set_last_home("")
p = window()
MainWindow._apply_devices(p, devices("我的家", "办公室", "全部"))
assert p._homes == ["办公室", "我的家"]
assert p._current_home == "办公室"
MainWindow._select_home(p, "我的家")
assert settings_store.get_last_home() == "我的家"
restored = window()
MainWindow._apply_devices(restored, devices("办公室", "我的家"))
assert restored._current_home == "我的家"
MainWindow._apply_devices(restored, devices("办公室", "我的家"))
assert restored._current_home == "我的家", "Refresh must preserve the selected group"

# 缓存缺少上次分组时先回退，联网列表补全后仍能恢复；不能把回退覆盖为用户选择。
cached = window()
MainWindow._apply_devices(cached, devices("办公室"))
assert cached._current_home == "办公室" and settings_store.get_last_home() == "我的家"
MainWindow._apply_devices(cached, devices("办公室", "我的家"))
assert cached._current_home == "我的家"
MainWindow._apply_devices(cached, devices("办公室"))
assert cached._current_home == "办公室"
MainWindow._select_home(cached, "全部")
assert cached._current_home == "办公室" and settings_store.get_last_home() == "我的家"
MainWindow._apply_devices(cached, [])
assert cached._homes == [] and cached._current_home == ""
cached._home_btn.setEnabled.assert_called_with(False)
assert settings_store.get_last_home() == "我的家"

settings_store.set_last_home("全部")
assert settings_store.get_last_home() == ""
settings_store.set_last_home("已删除的分组")
fallback = window()
MainWindow._apply_devices(fallback, devices("办公室", "我的家"))
assert fallback._current_home == fallback._homes[0]
with patch("app.ui.main_window.QMenu") as menu:
    MainWindow._show_home_menu(fallback)
    assert [c.args[0] for c in menu.return_value.addAction.call_args_list] == fallback._homes
    assert "全部" not in fallback._homes
print("HOME SELECTION REGRESSION ALL PASS")
