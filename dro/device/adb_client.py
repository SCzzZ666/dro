"""ADB 统一封装。

- 集中管理 adb 命令、超时、错误分类
- 提供 tap/swipe/type/keyevent/screencap 等原子操作
- 所有方法返回结构化结果或抛 AdbError（不抛裸 subprocess 异常）
"""
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..core.config import settings

# 常见 App 包名映射（open_app 使用）
APP_PACKAGES = {
    "淘宝": "com.taobao.taobao",
    "淘宝特价版": "com.taobao.tmall",
    "京东": "com.jingdong.app.mall",
    "拼多多": "com.xunmeng.pinduoduo",
    "闲鱼": "com.taobao.idlefish",
    "微信": "com.tencent.mm",
    "设置": "com.android.settings",
    "抖音": "com.ss.android.ugc.awlive",
}


class AdbError(RuntimeError):
    """ADB 相关错误，携带分类信息。"""

    CATEGORY = "adb"

    def __init__(self, message: str, category: str = "adb"):
        super().__init__(message)
        self.category = category


@dataclass
class AdbResult:
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    elapsed: float = 0.0

    def ok(self) -> bool:
        return self.returncode == 0


class AdbClient:
    """ADB 客户端。

    serial 为 None 时使用 adb 默认设备（仅当单设备时有效）。
    """

    # 在线态缓存窗口：TTL 内不再探测 adb devices（工具操作失败可 invalidate 强制重测）
    ONLINE_TTL = 3.0

    def __init__(self, serial: Optional[str] = None, timeout: float = 10.0):
        self.serial = serial or settings.device_serial
        self.timeout = settings.tool_timeout or timeout
        self._screen: Optional[Tuple[int, int]] = None
        self._online_ok_at: Optional[float] = None

    # ------------------------------------------------------------------
    # 底层执行
    # ------------------------------------------------------------------
    def _base_args(self) -> List[str]:
        return [settings.adb_binary, "-s", self.serial]

    def run(self, args: List[str], timeout: Optional[float] = None) -> AdbResult:
        cmd = [*self._base_args(), *args]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
            )
        except FileNotFoundError as e:
            raise AdbError("找不到 adb 命令，请安装 Android SDK Platform-tools 并配置环境变量。") from e
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"ADB 命令超时: {' '.join(cmd)}", category="timeout") from e
        return AdbResult(
            command=" ".join(cmd),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
            elapsed=time.monotonic() - started,
        )

    # ------------------------------------------------------------------
    # 设备管理
    # ------------------------------------------------------------------
    def devices(self) -> str:
        proc = subprocess.run(
            [settings.adb_binary, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return proc.stdout or ""

    def ensure_online(self, auto_reconnect: bool = True) -> bool:
        """检查设备在线，尝试自动重连网络模拟器。

        在线态做 TTL 缓存（ONLINE_TTL 内不再探测 adb devices），
        工具操作失败时可通过 invalidate_online() 强制下次重测。
        """
        now = time.monotonic()
        if self._online_ok_at is not None and now - self._online_ok_at < self.ONLINE_TTL:
            return True

        output = self.devices()
        for line in output.splitlines()[1:]:
            if self.serial in line and "device" in line and "offline" not in line:
                self._online_ok_at = now
                return True

        if auto_reconnect and ":" in self.serial and _looks_like_host_port(self.serial):
            self.run(["disconnect"], timeout=5)
            time.sleep(1)
            conn = subprocess.run(
                [settings.adb_binary, "connect", self.serial],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            _ = conn
            time.sleep(2)
            if self.serial in self.devices():
                self._online_ok_at = time.monotonic()
                return True
        return False

    def invalidate_online(self) -> None:
        """清除在线态缓存，强制下次 ensure_online 重新探测设备。"""
        self._online_ok_at = None

    def check_device(self, auto_connect: bool = True) -> None:
        """确保设备在线；失败抛 AdbError（供工具兜底返回错误信息）。"""
        if not self.ensure_online(auto_reconnect=auto_connect):
            raise AdbError(
                f"设备 {self.serial} 未在线，请检查 USB 调试/模拟器连接。", category="device_offline"
            )

    # ------------------------------------------------------------------
    # App 控制
    # ------------------------------------------------------------------
    def open_app(self, app_name: str, timeout: float = 15.0) -> AdbResult:
        """用 monkey 方启动式启动 App（无需 resolve activity 名）。"""
        package = APP_PACKAGES.get(app_name)
        if not package:
            # 中文名匹配失败时，尝试英文/包名直接传递
            package = app_name
        return self.run(
            ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=timeout,
        )

    def dump_ui(self) -> str:
        """uiautomator dump 当前界面 XML（元素定位的基础）。"""
        try:
            self.run(["shell", "uiautomator", "dump", "/sdcard/dro_ui.xml"], timeout=10)
            res = self.run(["shell", "cat", "/sdcard/dro_ui.xml"], timeout=5)
            return res.stdout or ""
        except AdbError:
            return ""

    def ensure_accessibility(self) -> Tuple[bool, str]:
        """检查/尝试开启 DroidRun 无障碍服务。返回 (是否就绪, 说明)。"""
        service = "com.droidrun.portal/com.droidrun.portal.service.DroidrunAccessibilityService"
        res = self.run(["shell", "settings", "get", "secure", "enabled_accessibility_services"], timeout=5)
        if service in res.stdout:
            return True, "DroidRun 无障碍服务已开启"
        self.run(
            ["shell", "settings", "put", "secure", "enabled_accessibility_services", service],
            timeout=5,
        )
        time.sleep(1)
        res2 = self.run(["shell", "settings", "get", "secure", "enabled_accessibility_services"], timeout=5)
        if "com.droidrun.portal" in res2.stdout:
            return True, "DroidRun 无障碍服务已自动开启"
        return False, "DroidRun 无障碍服务未开启且无法自动开启，请在模拟器中手动开启"

    # ------------------------------------------------------------------
    # 屏幕操作原子能力
    # ------------------------------------------------------------------
    def tap(self, x: int, y: int) -> AdbResult:
        return self.run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, direction: str = "up", duration: int = 300) -> AdbResult:
        w, h = self._screen_size()
        dx, dy = w // 2, h // 2
        if direction == "up":
            x1, y1, x2, y2 = dx, int(h * 0.78), dx, int(h * 0.22)
        elif direction == "down":
            x1, y1, x2, y2 = dx, int(h * 0.22), dx, int(h * 0.78)
        elif direction == "left":
            x1, y1, x2, y2 = int(w * 0.85), dy, int(w * 0.15), dy
        elif direction == "right":
            x1, y1, x2, y2 = int(w * 0.15), dy, int(w * 0.85), dy
        else:
            raise AdbError(f"不支持的滑动方向: {direction}")
        return self.run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])

    def type_text(self, text: str) -> AdbResult:
        # adb input text 只支持 ASCII；中文/特殊字符走剪贴板粘贴
        if any(ord(ch) > 127 for ch in text):
            set_res = self.set_clipboard(text)
            if not set_res.ok():
                return set_res
            return self.paste()
        # adb input text 对空格等字符需要转义
        escaped = text.replace(" ", "%s")
        return self.run(["shell", "input", "text", escaped])

    def set_clipboard(self, text: str) -> AdbResult:
        """把文本写入系统剪贴板，多级 fallback：

        1. `cmd clipboard set-text`（Android 10+ 标准接口）
        2. ADBKeyboard 广播（设备若装有该输入法；ADB_INPUT_TEXT 经剪贴板+IME 提交）
        3. 均失败：返回失败结果并附可读指引（中文输入需系统剪贴板能力）
        """
        # 1) 标准接口：Android 10+ 均有；部分精简 ROM 无
        r = self.run(["shell", "cmd", "clipboard", "set-text", text])
        if r.ok() and not any(
            kw in (r.stdout + r.stderr)
            for kw in ("No shell command", "not found", "Exception", "Error", "unknown command", "Bad")
        ):
            return r
        # 2) ADBKeyboard（开源 IME）：广播文本，由 IME 写入剪贴板并注入
        broadcast = self.run(
            ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text],
            timeout=8,
        )
        if broadcast.ok() and "Broadcast completed" in broadcast.stdout:
            return broadcast
        # 3) 明确失败：返回主错误 + 指引
        detail = (r.stderr or r.stdout or "").strip()
        guide = (
            "当前系统不支持命令行剪贴板，中文/特殊字符输入失败。"
            "可改用英文关键词，或在设备上安装 ADBKeyboard.apk 后重试。"
        )
        return AdbResult(
            command=f"set_clipboard({text[:20]!r})",
            stdout=r.stdout,
            stderr=f"{detail} | {guide}" if detail else guide,
            returncode=1,
        )

    def paste(self) -> AdbResult:
        """触发系统粘贴（需输入框已聚焦）。"""
        return self.keyevent(self.KEY_PASTE)

    def keyevent(self, keycode: int) -> AdbResult:
        return self.run(["shell", "input", "keyevent", str(keycode)])

    KEY_BACK = 4
    KEY_ENTER = 66
    KEY_HOME = 3
    KEY_PASTE = 279

    def go_back(self) -> AdbResult:
        return self.keyevent(self.KEY_BACK)

    def home(self) -> AdbResult:
        return self.keyevent(self.KEY_HOME)

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def screencap(self, save_path: Path) -> Path:
        """截图：adb exec-out screencap -p 直接拉流，免落地、免 rm（省两次子进程）。"""
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [*self._base_args(), "exec-out", "screencap", "-p"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            raise AdbError("screencap 超时", category="timeout") from e
        if proc.returncode != 0 or not proc.stdout:
            detail = (proc.stderr or b"").decode("utf-8", "replace")
            raise AdbError(f"screencap 失败: {detail}")
        save_path.write_bytes(proc.stdout)
        return save_path

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _screen_size(self) -> Tuple[int, int]:
        if self._screen is not None:
            return self._screen
        res = self.run(["shell", "wm", "size"], timeout=5)
        line = res.stdout.strip().splitlines()[-1] if res.stdout else ""
        if "x" in line:
            try:
                w, h = line.rsplit("x", 1)
                self._screen = (int(w), int(h))
                return self._screen
            except ValueError:
                pass
        return (1080, 2400)  # 兜底常见分辨率（不缓存，下次重测）

    def invalidate_screen_size(self) -> None:
        """清除分辨率缓存（设备/分辨率变化时调用）。"""
        self._screen = None

    def current_focus(self) -> str:
        """当前焦点窗口（含包名），用于 open_app 后轮询 App 是否就位。"""
        res = self.run(["shell", "dumpsys", "window"], timeout=5)
        for line in res.stdout.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                return line.strip()
        return ""


def _looks_like_host_port(value: str) -> bool:
    return value.replace(".", "").replace(":", "").isdigit()


def wait_for(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.3,
    description: str = "",
) -> bool:
    """轮询等待 predicate() 返回真值，超时返回 False。

    用于把固定 time.sleep 替换为「等到状态就绪」：状态提前就绪则提前返回，
    超过上限仍不满足则交由调用方走兜底。predicate 抛异常视为「未就绪」。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 —— 探测异常视为未就绪，继续等待
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def wait_for_ui_change(before: str, timeout: float = 4.0, interval: float = 0.4) -> bool:
    """等待界面 XML 相对 before 发生变化（页面跳转/加载完成的信号）。

    dump_ui 失败返回空串时视为「未变化」，避免误判；before 为空则直接返回 False。
    """
    if not before:
        return False

    def _changed() -> bool:
        cur = adb.dump_ui()
        return bool(cur) and cur != before

    return wait_for(_changed, timeout=timeout, interval=interval, description="等待界面变化")


adb = AdbClient()