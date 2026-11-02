# SPDX-License-Identifier: GPL-3.0-or-later
"""按需运行的本机摄像头桥接：仅视频预览，不录制、不启动麦克风。"""

import json
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, quote

import requests


class CameraError(Exception):
    """可以显示给用户的摄像头错误，不包含会话凭据。"""


@dataclass(frozen=True)
class CameraConnection:
    model: str
    did: str = field(repr=False)
    ip: str = field(repr=False)
    user_id: str = field(repr=False)
    pass_token: str = field(repr=False)
    region: str = "cn"

    def source(self, quality: str) -> str:
        if quality not in ("hd", "sd"):
            raise CameraError("不支持的视频清晰度")
        return (f"xiaomi://{quote(self.user_id, safe='')}:{self.region}@{self.ip}?"
                + urlencode({"did": self.did, "model": self.model,
                             "subtype": quality, "audio": "0"}))


@dataclass(frozen=True)
class RelayCameraConnection:
    model: str
    parameters: dict = field(repr=False)

    def source(self, quality: str) -> str:
        if quality not in ("hd", "sd"):
            raise CameraError("不支持的视频清晰度")
        return "xiaomi://relay?" + urlencode({**self.parameters, "model": self.model,
                                             "subtype": quality, "audio": "0"})


def is_camera(device) -> bool:
    return (".camera." in device.model
            or ":device:camera:" in getattr(device, "spec_type", ""))


def bridge_binary() -> Path:
    from app import resource_path
    path = resource_path("runtime/camera/go2rtc.exe")
    if not path.is_file():
        raise CameraError("尚未安装视频组件，请运行 tools\\build_camera_bridge.ps1 后重试")
    return path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CameraBridge:
    """一个预览拥有一个子进程；关闭后立即断流并清理临时认证配置。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._job = None
        self._closed = False
        self._api_url = ""
        self._session = requests.Session()
        self._session.trust_env = False  # localhost 请求不经过用户的 HTTP 代理
        self._session.auth = ("mihome", secrets.token_urlsafe(24))

    def start(self, connection: CameraConnection, quality: str = "hd") -> str:
        return self._start(connection.source(quality),
                           {connection.user_id: connection.pass_token})

    def start_relay(self, connection: RelayCameraConnection, quality: str) -> str:
        return self._start(connection.source(quality), {})

    def _start(self, source: str, accounts: dict) -> str:
        binary = bridge_binary()
        api_port, rtsp_port = _free_port(), _free_port()
        while rtsp_port == api_port:
            rtsp_port = _free_port()
        config = {
            "api": {"listen": f"127.0.0.1:{api_port}", "origin": "", "local_auth": True,
                    "username": self._session.auth[0], "password": self._session.auth[1]},
            "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
            "webrtc": {"listen": ""},
            "log": {"level": "error"},
            "xiaomi": accounts,
            "streams": {"camera": source},
        }
        # JSON 是 YAML 的子集；凭据不出现在命令行和日志中。
        with tempfile.TemporaryDirectory(prefix="mihome-camera-") as folder:
            path = Path(folder) / "session.yaml"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self._lock:
                if self._closed:
                    raise CameraError("预览已关闭")
                if self._process is not None:
                    raise CameraError("视频连接已启动")
                self._process = subprocess.Popen(
                    [str(binary), "-config", str(path)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    from .camera_process import CameraProcessJob
                    self._job = CameraProcessJob(self._process)
                except OSError:
                    self._process.terminate()
                    self._process.wait(timeout=2)
                    self._process = None
                    raise CameraError("无法建立视频进程保护，请重新启动应用后重试") from None
                self._api_url = f"http://127.0.0.1:{api_port}"
            try:
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    with self._lock:
                        if self._closed:
                            raise CameraError("预览已关闭")
                        if self._process.poll() is not None:
                            raise CameraError("视频组件未能启动，请检查组件安装或本机端口占用")
                    try:
                        response = self._session.get(self._api_url + "/api", timeout=0.5)
                        if response.ok:
                            return f"rtsp://127.0.0.1:{rtsp_port}/camera"
                    except requests.RequestException:
                        pass
                    time.sleep(0.1)
                raise CameraError("启动视频组件超时，请重试")
            except Exception:
                self.close()
                raise
        # 离开 with 时删除临时配置，只有桥接进程内存保留当前认证会话。

    def move(self, direction: str) -> None:
        operations = {"left": 1, "right": 2, "up": 3, "down": 4}
        if direction not in operations:
            raise CameraError("不支持的云台方向")
        with self._lock:
            if self._closed or self._process is None or self._process.poll() is not None:
                raise CameraError("请先打开实时预览")
        try:
            response = self._session.post(
                self._api_url + "/api/xiaomi/command",
                params={"src": "camera", "cmd": "0x112",
                        "data": json.dumps({"operation": operations[direction]})},
                timeout=5,
            )
            if response.status_code == 409:
                raise CameraError("视频连接尚未就绪，请等画面出现后再操作")
            response.raise_for_status()
            payload = response.json()
            if payload.get("hex"):
                data = bytes.fromhex(payload["hex"])
                if len(data) < 4 or int.from_bytes(data[:4], "big") != 0x113:
                    raise CameraError("收到非云台回包，已停止连续转动")
                reply = data[4:].decode("utf-8")
            else:
                reply = payload.get("reply", "")
            # 仅收到 HTTP 响应不代表电机执行成功，必须有设备的成功确认。
            if isinstance(reply, str):
                if reply.startswith("\x00\x00\x01\x13"):
                    reply = reply[4:]
                reply = reply.rstrip("\x00 \r\n\t")
                if not reply:
                    raise CameraError("未收到云台应答，指令可能已执行；已停止连续转动")
                reply = json.loads(reply)
            if not isinstance(reply, dict) or type(reply.get("ret")) is not int:
                raise CameraError("云台回包缺少有效状态，已停止连续转动")
            if reply["ret"] != 0:
                raise CameraError(f"摄像头返回云台状态 ret={reply['ret']}，已停止连续转动")
        except requests.Timeout:
            raise CameraError("等待云台确认超时，指令可能已执行；已停止连续转动") from None
        except requests.RequestException:
            raise CameraError("无法取得云台应答，请重新连接视频") from None
        except (ValueError, TypeError, AttributeError):
            raise CameraError("云台回包格式无效，已停止连续转动") from None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
            job, self._job = self._job, None
        if job is not None:
            job.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class RelayCameraSession(CameraBridge):
    """跨网络预览与云台复用同一个已认证 MISS 会话。"""

    def __init__(self, service, did):
        super().__init__()
        self._service, self._did = service, did

    def start(self, quality: str) -> str:
        with self._lock:
            if self._closed:
                raise CameraError("预览已关闭")
        connection = self._service.camera_relay_connection(self._did)
        return self.start_relay(connection, quality)
