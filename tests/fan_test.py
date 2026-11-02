# SPDX-License-Identifier: GPL-3.0-or-later
"""两季扇主开关映射回归：风机、加热和指示灯的 on 不可混用。

用法：python -m tests.fan_test；全部使用假设备，不访问网络或控制真实设备。
"""

import threading
from copy import deepcopy

from tests._isolate import isolate

isolate()

from app.core.service import MijiaService, ServiceError


SPEC_TYPE = "urn:miot-spec-v2:device:fan:0000A005:dmaker-p23:1"
# 对应官方规格的 heater / fan / indicator-light 三个独立服务。
SPEC = {"spec_type": SPEC_TYPE, "properties": [
    {"name": f"on-{siid}", "type": "bool", "rw": "rw",
     "method": {"siid": siid, "piid": 1}} for siid in (2, 5, 9)
]}


class FakeApi:
    def __init__(self):
        self.values = {2: False, 5: True, 9: True}
        self.reads = []
        self.writes = []

    def get_devices_prop(self, params):
        self.reads.extend(params)
        return [{**q, "code": 0, "value": self.values[q["siid"]]} for q in params]

    def set_devices_prop(self, params):
        self.writes.extend(params)
        for q in params:
            self.values[q["siid"]] = q["value"]
        return [{"code": 0} for _ in params]


def service(spec=SPEC):
    svc = object.__new__(MijiaService)
    svc._lock = threading.RLock()
    svc._api = FakeApi()
    svc._device_index = {"fan": ("dmaker.fan.p23", SPEC_TYPE, "Test fan")}
    svc._specs_for = lambda dids: {d: spec for d in dids}
    return svc


assert MijiaService._find_on_method(SPEC, "fan") == {"siid": 5, "piid": 1}
svc = service()
assert svc.power_states(["fan"]) == {"fan": True}, "Heating off must not mean fan off"
svc._api.values.update({2: True, 5: False, 9: True})
assert svc.power_state("fan") is False, "Heater/indicator on must not mean fan on"
assert all(q["siid"] == 5 and q["piid"] == 1 for q in svc._api.reads)
print("1. Fan status uses fan service, independent of heater/indicator OK")

svc = service()
svc.set_power_state("fan", False)
assert svc._api.writes == [{"did": "fan", "siid": 5, "piid": 1, "value": False}]
assert svc._api.values == {2: False, 5: False, 9: True}
assert svc.toggle_power("fan") is True
assert svc._api.values == {2: False, 5: True, 9: True}
assert svc.toggle_power("fan", current=True) is False
assert all(q["siid"] == 5 and q["piid"] == 1 for q in svc._api.writes)
print("2. Set/toggle use the same main switch without changing heating/indicator OK")

missing = deepcopy(SPEC)
missing["properties"] = [p for p in missing["properties"] if p["method"]["siid"] != 5]
assert MijiaService._find_on_method(missing, "fan") is None
svc = service(missing)
try:
    svc.set_power_state("fan", True)
except ServiceError:
    pass
else:
    raise AssertionError("Missing main switch must not fall back to heater control")
assert not svc._api.writes
print("3. Missing primary switch cannot accidentally control heating OK")

# 未适配规格维持现有默认选择；虚拟子设备优先按通道号选择。
generic = {"properties": deepcopy(SPEC["properties"])}
assert MijiaService._find_on_method(generic, "other") == {"siid": 2, "piid": 1}
assert MijiaService._find_on_method(SPEC, "parent.s2") == {"siid": 2, "piid": 1}
assert MijiaService._find_on_method(SPEC, "parent.s5") == {"siid": 5, "piid": 1}
assert MijiaService._find_on_method({"properties": []}, "other") is None
print("4. Generic and multi-channel device selection unchanged OK")
print("FAN REGRESSION ALL PASS")
