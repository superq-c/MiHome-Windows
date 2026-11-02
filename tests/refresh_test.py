# SPDX-License-Identifier: GPL-3.0-or-later
r"""开关刷新回归：缓存启动、手动刷新、失败重试和在途请求合并。

用法：.venv\Scripts\python.exe -m tests.refresh_test
使用真实窗口方法及服务方法，替换 UI、任务队列和网络边界，不访问真实账号。
"""

import os
import threading
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from tests._isolate import isolate

isolate()

from app.core.models import DeviceInfo
from app.core.service import MijiaService
from app.ui.main_window import MainWindow


class Jobs:
    def __init__(self):
        self.tasks = []

    def submit(self, fn, on_success=None, on_error=None, **kwargs):
        self.tasks.append((fn, on_success, on_error))

    def finish(self):
        fn, success, error = self.tasks.pop(0)
        try:
            result = fn()
        except Exception as exc:
            if error is None:
                raise
            error(exc)
        else:
            if success is not None:
                success(result)


DEVICE = DeviceInfo("d", "Test light", "test.light", "Home", "Room", True, "urn:test")


def window(known):
    probe = MagicMock()
    probe._known_power = dict(known)
    probe._metrics = {}
    probe._power_gen = {}
    probe._poll_in_flight = False
    probe._power_refresh_pending = False
    probe._all_devices = [DEVICE]
    probe._cards = {}
    probe._status_panel = None
    probe._board = None
    probe._loading_devices = False
    probe._current_home = "全部"
    probe._current_room = "全屋"
    probe._room_initialized = True
    probe._natural_tabs.return_value = ["全屋"]
    probe._diff_devices.return_value = (0, 0)
    probe._poll_dids = lambda: [d.did for d in probe._all_devices if d.online]
    probe._jobs = Jobs()
    probe._service = MagicMock()
    probe._service.power_states.return_value = {"d": True}
    probe._service.list_devices.return_value = [DEVICE]
    for name in ("_startup_load", "load_devices", "_on_devices_loaded", "_apply_devices",
                 "_refresh_power_states", "_on_poll_tick", "_apply_power_states",
                 "_apply_power_state", "_on_poll_error", "_run_pending_power_refresh"):
        setattr(probe, name, MethodType(getattr(MainWindow, name), probe))
    return probe


# 缓存里已有 False / 错误的 None，启动后仍同步设备列表和真实开关值。
for cached_state in (False, None):
    p = window({})
    with patch("app.ui.main_window.device_cache.load", return_value=(
            [DEVICE], {"d": cached_state}, {})), \
            patch("app.ui.main_window.device_cache.save"), \
            patch("app.ui.main_window.Toast.info"):
        p._startup_load()
        while p._jobs.tasks:
            p._jobs.finish()
    assert p._known_power["d"] is True
    p._service.list_devices.assert_called_once()
    p._service.power_states.assert_called()
print("1. Cached startup refreshes device metadata and power OK")

# 缓存中的设备已离线：在线列表同步后必须恢复轮询。
p = window({})
offline = DeviceInfo("d", "Test light", "test.light", "Home", "Room", False, "urn:test")
with patch("app.ui.main_window.device_cache.load", return_value=(
        [offline], {"d": False}, {})), \
        patch("app.ui.main_window.device_cache.save"), \
        patch("app.ui.main_window.Toast.info"):
    p._startup_load()
    while p._jobs.tasks:
        p._jobs.finish()
assert p._all_devices[0].online and p._known_power["d"] is True

# 手动刷新不能保留旧 False 或历史无能力结论。
for old_state in (False, None):
    p = window({"d": old_state})
    with patch("app.ui.main_window.device_cache.save"), \
            patch("app.ui.main_window.Toast.info"):
        p.load_devices()
        while p._jobs.tasks:
            p._jobs.finish()
    assert p._known_power["d"] is True
print("2. Manual refresh rereads cached states OK")

# 首次整批读取异常、单台响应失败（空结果）均不能让未知设备失去重试机会。
for failure in (RuntimeError("temporary failure"), {}):
    p = window({})
    p._service.power_states.side_effect = [failure, {"d": True}]
    p._refresh_power_states(force=False)
    p._jobs.finish()
    assert "d" not in p._known_power
    p._on_poll_tick()
    p._jobs.finish()
    assert p._known_power["d"] is True

p = window({"d": None})
p._on_poll_tick()
assert not p._jobs.tasks, "Confirmed unsupported devices should not be polled"
print("3. Failed discovery retries; confirmed unsupported devices skip polling OK")

# 请求在途时多次刷新合并，成功/异常返回后都只补读一次。
for first_result in ({"d": False}, RuntimeError("temporary failure")):
    p = window({"d": False})
    p._service.power_states.side_effect = [first_result, {"d": True}]
    p._refresh_power_states(force=True)
    p._refresh_power_states(force=True)
    p._refresh_power_states(force=True)
    assert len(p._jobs.tasks) == 1
    p._jobs.finish()
    assert len(p._jobs.tasks) == 1
    p._jobs.finish()
    assert p._known_power["d"] is True and not p._jobs.tasks
    assert not p._poll_in_flight and not p._power_refresh_pending
print("4. In-flight refreshes coalesce and finish after success/failure OK")

# 修复刷新不能破坏现有的操作版本保护：旧轮询不得覆盖用户刚操作的状态。
p = window({"d": True})
p._power_gen = {"d": 1}
p._apply_power_states({"d": False}, {"d": 0})
assert p._known_power["d"] is True
p._apply_power_states({"d": False}, {"d": 1})
assert p._known_power["d"] is False
print("4b. Stale polling results cannot overwrite local operations OK")

# 使用真实规格获取与 power_states 路径，第一次请求超时，第二次恢复。
svc = object.__new__(MijiaService)
svc._lock = threading.RLock()
svc._device_index = {"d": ("test.light", "urn:test", "Test light")}
svc._spec_cache = {}
svc._api = MagicMock()
svc._api.auth_data_path = SimpleNamespace(parent=isolate())
svc._api.get_devices_prop.return_value = [
    {"did": "d", "siid": 2, "piid": 1, "code": 0, "value": True}]
spec = {"properties": [{"name": "on", "type": "bool", "rw": "rw",
                         "method": {"siid": 2, "piid": 1}}]}
with patch("app.core.service.get_device_info", side_effect=[TimeoutError(), spec]), \
        patch("app.core.service.spec_zh.apply_fallback"):
    assert svc.power_states(["d"]) == {}, "Spec request failure must remain unknown"
    assert "urn:test" not in svc._spec_cache
    assert svc.spec_has_published_functions("urn:test") is None
    assert svc.power_states(["d"]) == {"d": True}
    assert svc.spec_has_published_functions("urn:test") is True

# 真正没有开关属性的规格仍保留 None，避免每次轮询重复请求。
svc._spec_cache["urn:test"] = {"properties": []}
assert svc.power_states(["d"]) == {"d": None}
print("5. Spec failure retries without poisoning capability cache OK")
print("REFRESH REGRESSION ALL PASS")
