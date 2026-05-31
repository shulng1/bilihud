# Live Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tray-opened PyQt6 live control dialog that lets a logged-in user set Bilibili live title/area, start or stop live streaming, and copy returned RTMP/SRT credentials.

**Architecture:** Put Bilibili live HTTP logic and stream parsing in a PyQt-free `live_api.py` module with unit tests. Move cookie-loading reuse into `AuthManager`, then build `LiveControlDialog` as a separate utility window opened from the existing tray menu so the overlay stays focused on danmaku.

**Tech Stack:** Python 3.10+, PyQt6, qasync, aiohttp, qrcode/Pillow, pytest, existing keyring/browser-cookie3 auth path.

---

## File Structure

- Create `src/bilihud/live_api.py`: app signing, typed result dataclasses, Bilibili live API wrappers, stream credential parsing, safe cookie lookup.
- Create `tests/test_live_api.py`: pure unit tests for signing, stream parsing, and face-auth URL formatting.
- Modify `src/bilihud/auth.py`: expose reusable cookie loading and authenticated session creation.
- Create `tests/test_auth.py`: unit tests for keyring-first and browser fallback cookie loading.
- Modify `src/bilihud/danmaku_client.py`: reuse `AuthManager` session/cookie helpers instead of owning duplicate cookie-loading logic.
- Create `src/bilihud/live_control_dialog.py`: PyQt6 dialog for title/area/start/stop/credential copy and verification prompts.
- Modify `src/bilihud/danmaku_widget.py`: add tray action and method to open the dialog.

---

### Task 1: Live API Pure Logic

**Files:**
- Create: `tests/test_live_api.py`
- Create: `src/bilihud/live_api.py`

- [ ] **Step 1: Write failing tests for app signing and stream parsing**

Create `tests/test_live_api.py` with this content:

```python
from bilihud.live_api import app_sign, format_face_auth_url, parse_stream_credentials


def test_app_sign_sorts_params_adds_appkey_and_signs():
    params = {"ts": "1700000000000", "system_version": "2"}

    signed = app_sign(params)

    assert (
        signed
        == "appkey=aae92bc66f3edfab&system_version=2&ts=1700000000000"
        "&sign=0145560363728c74c6e3f829a34d8991"
    )
    assert params == {"ts": "1700000000000", "system_version": "2"}


def test_parse_stream_credentials_extracts_primary_and_protocol_streams():
    payload = {
        "rtmp": {"addr": "rtmp://primary", "code": "primary-key"},
        "protocols": [
            {"protocol": "rtmp", "addr": "rtmp://backup", "code": "backup-key"},
            {"protocol": "srt", "addr": "srt://primary", "code": "srt-key"},
        ],
    }

    credentials = parse_stream_credentials(payload)

    assert [(item.label, item.address, item.key) for item in credentials] == [
        ("rtmp-1", "rtmp://primary", "primary-key"),
        ("rtmp-2", "rtmp://backup", "backup-key"),
        ("srt-1", "srt://primary", "srt-key"),
    ]


def test_parse_stream_credentials_skips_invalid_or_unknown_protocols():
    payload = {
        "rtmp": {"addr": "", "code": "missing-address"},
        "protocols": [
            {"protocol": "rtmp", "addr": "rtmp://valid", "code": "valid-key"},
            {"protocol": "rtmp", "addr": "rtmp://missing-key", "code": ""},
            {"protocol": "srt", "addr": "", "code": "missing-address"},
            {"protocol": "hls", "addr": "https://ignored", "code": "ignored"},
        ],
    }

    credentials = parse_stream_credentials(payload)

    assert [(item.label, item.address, item.key) for item in credentials] == [
        ("rtmp-1", "rtmp://valid", "valid-key"),
    ]


def test_format_face_auth_url_uses_uid():
    assert (
        format_face_auth_url("12345")
        == "https://www.bilibili.com/blackboard/live/face-auth-middle.html?source_event=400&mid=12345"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_live_api.py -v
```

Expected result: FAIL with `ModuleNotFoundError: No module named 'bilihud.live_api'`.

- [ ] **Step 3: Create `live_api.py` implementation**

Create `src/bilihud/live_api.py` with this content:

```python
# -*- coding: utf-8 -*-
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

import aiohttp

BASE_URL = "https://api.live.bilibili.com"
APP_KEY = "aae92bc66f3edfab"
APP_SECRET = "af125a0d5279fd576c1b4418a3e8276d"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)


@dataclass(frozen=True)
class StreamCredential:
    label: str
    address: str
    key: str


@dataclass(frozen=True)
class LiveVersion:
    curr_version: str
    build: int


@dataclass(frozen=True)
class StartLiveResult:
    code: int
    message: str
    data: dict[str, Any]


class LiveApiError(RuntimeError):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def app_sign(params: Mapping[str, str]) -> str:
    signed_params = {str(key): str(value) for key, value in params.items()}
    signed_params["appkey"] = APP_KEY
    query = urlencode(sorted(signed_params.items()))
    sign = hashlib.md5((query + APP_SECRET).encode("utf-8")).hexdigest()
    return f"{query}&sign={sign}"


def format_face_auth_url(uid: str | int) -> str:
    return (
        "https://www.bilibili.com/blackboard/live/face-auth-middle.html"
        f"?source_event=400&mid={uid}"
    )


def get_cookie_value(session: aiohttp.ClientSession, name: str) -> Optional[str]:
    for cookie in session.cookie_jar:
        if cookie.key == name:
            return cookie.value
    return None


def parse_stream_credentials(start_live_data: Mapping[str, Any]) -> list[StreamCredential]:
    credentials: list[StreamCredential] = []
    counters = {"rtmp": 0, "srt": 0}

    rtmp = start_live_data.get("rtmp")
    if isinstance(rtmp, Mapping):
        addr = str(rtmp.get("addr") or "")
        code = str(rtmp.get("code") or "")
        if addr and code:
            counters["rtmp"] += 1
            credentials.append(StreamCredential("rtmp-1", addr, code))

    protocols = start_live_data.get("protocols") or []
    if not isinstance(protocols, list):
        protocols = []

    for protocol_data in protocols:
        if not isinstance(protocol_data, Mapping):
            continue
        protocol = str(protocol_data.get("protocol") or "").lower()
        if protocol not in counters:
            continue
        addr = str(protocol_data.get("addr") or "")
        code = str(protocol_data.get("code") or "")
        if not addr or not code:
            continue
        counters[protocol] += 1
        credentials.append(StreamCredential(f"{protocol}-{counters[protocol]}", addr, code))

    return sorted(credentials, key=lambda item: item.label)


async def _request_json(
    session: aiohttp.ClientSession,
    method: str,
    endpoint: str,
    *,
    data: Optional[Mapping[str, str]] = None,
    headers: Optional[Mapping[str, str]] = None,
    require_sign: bool = False,
    raw: bool = False,
) -> Any:
    url = f"{BASE_URL}{endpoint}"
    request_headers = {
        "Accept": "*/*",
        "User-Agent": USER_AGENT,
        **dict(headers or {}),
    }
    body = None

    if method.upper() != "GET" and data is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = app_sign(data) if require_sign else urlencode(data)

    async with session.request(method.upper(), url, headers=request_headers, data=body) as response:
        if response.status != 200:
            raise LiveApiError(f"HTTP错误: {response.status}")
        payload = await response.json()

    if raw:
        return payload

    if payload.get("code") != 0 or payload.get("data") is None:
        raise LiveApiError(
            f"API错误: {payload.get('message') or 'Unknown Error'} ({payload.get('code')})",
            payload.get("code"),
        )

    return payload["data"]


async def get_area_list(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
    data = await _request_json(
        session,
        "GET",
        "/room/v1/Area/getList?show_pinyin=1",
        headers={"Origin": BASE_URL},
    )
    return list(data)


async def get_live_version(session: aiohttp.ClientSession, now_ms: Optional[int] = None) -> LiveVersion:
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    query = app_sign({"system_version": "2", "ts": timestamp})
    data = await _request_json(
        session,
        "GET",
        f"/xlive/app-blink/v1/liveVersionInfo/getHomePageLiveVersion?{query}",
        headers={"Origin": BASE_URL},
    )
    return LiveVersion(curr_version=str(data["curr_version"]), build=int(data["build"]))


def require_csrf(session: aiohttp.ClientSession) -> str:
    csrf = get_cookie_value(session, "bili_jct")
    if not csrf:
        raise LiveApiError("未找到CSRF Token，请先扫码登录")
    return csrf


async def update_room_title(session: aiohttp.ClientSession, room_id: int, title: str) -> None:
    csrf = require_csrf(session)
    await _request_json(
        session,
        "POST",
        "/room/v1/Room/update",
        headers={"Origin": BASE_URL},
        data={
            "room_id": str(room_id),
            "csrf": csrf,
            "csrf_token": csrf,
            "title": title,
            "platform": "pc_link",
        },
    )


async def update_room_area(session: aiohttp.ClientSession, room_id: int, area_id: str) -> None:
    csrf = require_csrf(session)
    await _request_json(
        session,
        "POST",
        "/room/v1/Room/update",
        headers={"Origin": BASE_URL},
        data={
            "room_id": str(room_id),
            "csrf": csrf,
            "csrf_token": csrf,
            "area_id": area_id,
            "platform": "pc_link",
        },
    )


async def start_live(
    session: aiohttp.ClientSession,
    room_id: int,
    area_id: str,
    version: str,
    build: str,
    now_ms: Optional[int] = None,
) -> StartLiveResult:
    csrf = require_csrf(session)
    timestamp = str(now_ms if now_ms is not None else int(time.time() * 1000))
    payload = await _request_json(
        session,
        "POST",
        "/room/v1/Room/startLive",
        headers={"Origin": BASE_URL},
        data={
            "room_id": str(room_id),
            "platform": "pc_link",
            "backup_stream": "0",
            "csrf": csrf,
            "csrf_token": csrf,
            "area_v2": area_id,
            "version": version,
            "build": build,
            "ts": timestamp,
        },
        require_sign=True,
        raw=True,
    )
    return StartLiveResult(
        code=int(payload.get("code", -1)),
        message=str(payload.get("message") or ""),
        data=dict(payload.get("data") or {}),
    )


async def stop_live(session: aiohttp.ClientSession, room_id: int) -> None:
    csrf = require_csrf(session)
    await _request_json(
        session,
        "POST",
        "/room/v1/Room/stopLive",
        headers={"Origin": BASE_URL},
        data={
            "room_id": str(room_id),
            "csrf": csrf,
            "platform": "pc_link",
            "csrf_token": csrf,
        },
    )
```

- [ ] **Step 4: Run live API tests**

Run:

```bash
uv run pytest tests/test_live_api.py -v
```

Expected result: all 4 tests PASS.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add src/bilihud/live_api.py tests/test_live_api.py
git commit -m "feat: add live api helpers"
```

---

### Task 2: Reusable Authentication Session

**Files:**
- Create: `tests/test_auth.py`
- Modify: `src/bilihud/auth.py`
- Modify: `src/bilihud/danmaku_client.py`

- [ ] **Step 1: Write failing tests for shared cookie loading**

Create `tests/test_auth.py` with this content:

```python
from dataclasses import dataclass

from bilihud.auth import AuthManager


@dataclass
class FakeCookie:
    name: str
    value: str


def test_load_auth_cookies_prefers_keyring(monkeypatch):
    manager = AuthManager()
    monkeypatch.setattr(manager, "load_cookies", lambda: {"SESSDATA": "keyring-sess", "bili_jct": "csrf"})

    cookies, from_keyring = manager.load_auth_cookies()

    assert cookies == {"SESSDATA": "keyring-sess", "bili_jct": "csrf"}
    assert from_keyring is True


def test_load_auth_cookies_uses_browser_when_keyring_is_empty(monkeypatch):
    manager = AuthManager()
    monkeypatch.setattr(manager, "load_cookies", lambda: None)
    monkeypatch.setattr(
        "bilihud.auth.load_bilibili_cookies",
        lambda: [FakeCookie("SESSDATA", "browser-sess"), FakeCookie("bili_jct", "browser-csrf")],
    )

    cookies, from_keyring = manager.load_auth_cookies()

    assert cookies == {"SESSDATA": "browser-sess", "bili_jct": "browser-csrf"}
    assert from_keyring is False


def test_load_auth_cookies_returns_empty_state_when_sources_fail(monkeypatch):
    manager = AuthManager()
    monkeypatch.setattr(manager, "load_cookies", lambda: None)

    def raise_browser_error():
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr("bilihud.auth.load_bilibili_cookies", raise_browser_error)

    cookies, from_keyring = manager.load_auth_cookies()

    assert cookies == {}
    assert from_keyring is False
```

- [ ] **Step 2: Run auth tests to verify they fail**

Run:

```bash
uv run pytest tests/test_auth.py -v
```

Expected result: FAIL with `AttributeError: 'AuthManager' object has no attribute 'load_auth_cookies'`.

- [ ] **Step 3: Add reusable auth helpers to `auth.py`**

In `src/bilihud/auth.py`, update the imports near the top to include `http.cookies` and `Mapping`:

```python
import asyncio
import aiohttp
import qrcode
import qrcode
import json
import logging
import time
import http.cookies
from typing import Optional, Dict, Tuple, Any, Mapping
from io import BytesIO
from PIL import Image
```

Add this constant after `USERNAME_KEY = "bilibili_cookies"`:

```python
COMMON_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)
```

Add these methods inside `AuthManager`, immediately after `load_cookies()`:

```python
    def load_auth_cookies(self, prefer_keyring: bool = True) -> Tuple[Dict[str, str], bool]:
        """
        Load Bilibili cookies from keyring first, then browser cookies.
        Returns: (cookies, loaded_from_keyring)
        """
        if prefer_keyring:
            saved_cookies = self.load_cookies()
            if saved_cookies:
                return dict(saved_cookies), True

        try:
            browser_cookies = load_bilibili_cookies()
            if browser_cookies:
                return {cookie.name: cookie.value for cookie in browser_cookies}, False
        except Exception as e:
            logger.error(f"Browser cookie load failed: {e}")

        return {}, False

    def create_session_from_cookies(self, cookies: Mapping[str, str]) -> aiohttp.ClientSession:
        session = aiohttp.ClientSession(headers={"User-Agent": COMMON_USER_AGENT})
        cookie_jar = http.cookies.SimpleCookie()
        for key, value in cookies.items():
            cookie_jar[key] = value
        if "SESSDATA" in cookie_jar:
            cookie_jar["SESSDATA"]["domain"] = "bilibili.com"
        session.cookie_jar.update_cookies(cookie_jar)
        return session

    async def create_authenticated_session(
        self,
        *,
        validate_keyring: bool = True,
    ) -> Tuple[aiohttp.ClientSession, Dict[str, str], bool]:
        loop = asyncio.get_running_loop()
        cookies, from_keyring = await loop.run_in_executor(None, self.load_auth_cookies)

        if from_keyring and validate_keyring and not await self.validate_session(cookies):
            logger.info("Keyring cookies expired")
            cookies = {}

        return self.create_session_from_cookies(cookies), cookies, from_keyring
```

Add this module-level function at the end of `auth.py`:

```python
def load_bilibili_cookies():
    """尝试从浏览器加载B站Cookies"""
    try:
        import browser_cookie3
        return browser_cookie3.chrome(domain_name=".bilibili.com")
    except Exception as e:
        logger.error(f"Chrome cookies failed: {e}")
        try:
            import browser_cookie3
            return browser_cookie3.edge(domain_name=".bilibili.com")
        except Exception as e:
            logger.error(f"Edge cookies failed: {e}")
            try:
                import browser_cookie3
                return browser_cookie3.firefox(domain_name=".bilibili.com")
            except Exception as e:
                logger.error(f"Firefox cookies failed: {e}")
                return None
```

- [ ] **Step 4: Refactor `DanmakuClient.start()` to use `AuthManager`**

In `src/bilihud/danmaku_client.py`, remove `import http.cookies`.

Replace the cookie-loading block in `DanmakuClient.start()` from the comment `# 初始化session` through `self.session.cookie_jar.update_cookies(cookies)` with this code:

```python
        from .auth import AuthManager

        auth_manager = AuthManager()
        self.session, loaded_cookies, is_keyring = await auth_manager.create_authenticated_session()

        if is_keyring and not loaded_cookies and self.on_login_failed:
            self.on_login_failed("本地保存的登录信息已失效，请重新登录")

        if self.sessdata:
            self.session.cookie_jar.update_cookies({"SESSDATA": self.sessdata})
```

Delete the `load_bilibili_cookies()` function from the bottom of `src/bilihud/danmaku_client.py`; the shared version now lives in `auth.py`.

- [ ] **Step 5: Run auth and existing tests**

Run:

```bash
uv run pytest tests/test_auth.py tests/test_utils.py -v
```

Expected result: all tests PASS.

- [ ] **Step 6: Run a syntax check for changed modules**

Run:

```bash
uv run python -m py_compile src/bilihud/auth.py src/bilihud/danmaku_client.py
```

Expected result: command exits with code 0 and prints no errors.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add src/bilihud/auth.py src/bilihud/danmaku_client.py tests/test_auth.py
git commit -m "refactor: share bilibili auth cookies"
```

---

### Task 3: Live Control Dialog

**Files:**
- Create: `src/bilihud/live_control_dialog.py`

- [ ] **Step 1: Verify the dialog module is not present**

Run:

```bash
uv run python -m py_compile src/bilihud/live_control_dialog.py
```

Expected result: FAIL because `src/bilihud/live_control_dialog.py` does not exist.

- [ ] **Step 2: Create `LiveControlDialog`**

Create `src/bilihud/live_control_dialog.py` with this content:

```python
# -*- coding: utf-8 -*-
import asyncio
import logging
from typing import Optional

import aiohttp
import qasync
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .auth import AuthManager
from .live_api import (
    LiveApiError,
    StreamCredential,
    format_face_auth_url,
    get_area_list,
    get_cookie_value,
    get_live_version,
    parse_stream_credentials,
    start_live,
    stop_live,
    update_room_area,
    update_room_title,
)
from .utils import load_config, save_config, validate_room_id

logger = logging.getLogger(__name__)


class LiveControlDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("直播控制")
        self.setMinimumSize(520, 560)
        self.auth_manager = AuthManager()
        self.session: Optional[aiohttp.ClientSession] = None
        self.area_list: list[dict] = []
        self.credentials: list[StreamCredential] = []
        self._loaded = False

        self._init_ui()
        self._load_config_values()
        self._update_action_state()

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(10)

        self.status_label = QLabel("正在准备直播控制...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #dddddd;")
        self.main_layout.addWidget(self.status_label)

        form = QFrame(self)
        form.setStyleSheet("QFrame { background: #2b2b2b; border-radius: 8px; }")
        form_layout = QGridLayout(form)
        form_layout.setContentsMargins(12, 12, 12, 12)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(10)

        self.room_id_input = QLineEdit()
        self.room_id_input.setPlaceholderText("直播间 ID")
        self.room_id_input.textChanged.connect(self._update_action_state)
        form_layout.addWidget(QLabel("房间号"), 0, 0)
        form_layout.addWidget(self.room_id_input, 0, 1, 1, 2)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("直播标题")
        self.title_input.textChanged.connect(self._update_action_state)
        form_layout.addWidget(QLabel("标题"), 1, 0)
        form_layout.addWidget(self.title_input, 1, 1)

        self.update_title_btn = QPushButton("更新标题")
        self.update_title_btn.clicked.connect(self.handle_update_title)
        form_layout.addWidget(self.update_title_btn, 1, 2)

        self.parent_area_combo = QComboBox()
        self.parent_area_combo.currentIndexChanged.connect(self._on_parent_area_changed)
        form_layout.addWidget(QLabel("分类"), 2, 0)
        form_layout.addWidget(self.parent_area_combo, 2, 1, 1, 2)

        self.area_combo = QComboBox()
        self.area_combo.currentIndexChanged.connect(self._update_action_state)
        form_layout.addWidget(QLabel("分区"), 3, 0)
        form_layout.addWidget(self.area_combo, 3, 1)

        self.update_area_btn = QPushButton("更新分区")
        self.update_area_btn.clicked.connect(self.handle_update_area)
        form_layout.addWidget(self.update_area_btn, 3, 2)

        action_row = QHBoxLayout()
        self.start_btn = QPushButton("开始直播")
        self.start_btn.clicked.connect(self.handle_start_live)
        self.stop_btn = QPushButton("停止直播")
        self.stop_btn.clicked.connect(self.handle_stop_live)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.stop_btn)
        form_layout.addLayout(action_row, 4, 0, 1, 3)

        self.main_layout.addWidget(form)

        self.credentials_title = QLabel("推流凭证")
        self.credentials_title.setStyleSheet("font-weight: bold;")
        self.main_layout.addWidget(self.credentials_title)

        self.credentials_scroll = QScrollArea(self)
        self.credentials_scroll.setWidgetResizable(True)
        self.credentials_container = QWidget()
        self.credentials_layout = QVBoxLayout(self.credentials_container)
        self.credentials_layout.setContentsMargins(0, 0, 0, 0)
        self.credentials_layout.setSpacing(8)
        self.credentials_scroll.setWidget(self.credentials_container)
        self.main_layout.addWidget(self.credentials_scroll, 1)

        self.empty_credentials_label = QLabel("开播成功后会在这里显示 RTMP/SRT 地址和密钥。")
        self.empty_credentials_label.setWordWrap(True)
        self.credentials_layout.addWidget(self.empty_credentials_label)

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        close_row.addWidget(self.close_btn)
        self.main_layout.addLayout(close_row)

    def _load_config_values(self):
        config = load_config()
        room_id = config.get("room_id", "")
        self.room_id_input.setText(str(room_id) if room_id else "")
        self.title_input.setText(str(config.get("live_title", "")))

    def set_room_id(self, room_id: int):
        if room_id > 0:
            self.room_id_input.setText(str(room_id))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            asyncio.create_task(self.load_initial_state())

    def closeEvent(self, event):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())
        super().closeEvent(event)

    async def load_initial_state(self):
        self._set_busy(True, "正在加载登录状态和直播分区...")
        try:
            self.session, cookies, _from_keyring = await self.auth_manager.create_authenticated_session()
            if not cookies or not get_cookie_value(self.session, "bili_jct"):
                self.set_status("请先通过托盘菜单扫码登录。")
            else:
                self.set_status("登录状态可用。")

            self.area_list = await get_area_list(self.session)
            self._populate_parent_areas()
            self._restore_saved_area()
        except Exception as e:
            logger.exception("Failed to initialize live control dialog")
            self.set_status(f"初始化失败: {e}", error=True)
        finally:
            self._set_busy(False)
            self._update_action_state()

    def _populate_parent_areas(self):
        self.parent_area_combo.blockSignals(True)
        self.parent_area_combo.clear()
        for parent in self.area_list:
            self.parent_area_combo.addItem(str(parent.get("name") or ""), str(parent.get("id") or ""))
        self.parent_area_combo.blockSignals(False)
        self._on_parent_area_changed()

    def _restore_saved_area(self):
        config = load_config()
        parent_id = str(config.get("live_parent_area_id", ""))
        area_id = str(config.get("live_area_id", ""))
        if parent_id:
            index = self.parent_area_combo.findData(parent_id)
            if index >= 0:
                self.parent_area_combo.setCurrentIndex(index)
        if area_id:
            index = self.area_combo.findData(area_id)
            if index >= 0:
                self.area_combo.setCurrentIndex(index)

    def _on_parent_area_changed(self):
        current_parent_id = self.parent_area_combo.currentData()
        selected_parent = None
        for parent in self.area_list:
            if str(parent.get("id") or "") == str(current_parent_id or ""):
                selected_parent = parent
                break

        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        if selected_parent:
            for area in selected_parent.get("list") or []:
                self.area_combo.addItem(str(area.get("name") or ""), str(area.get("id") or ""))
        self.area_combo.blockSignals(False)
        self._update_action_state()

    def _room_id(self) -> Optional[int]:
        text = self.room_id_input.text().strip()
        if not validate_room_id(text):
            return None
        return int(text)

    def _selected_area_id(self) -> str:
        return str(self.area_combo.currentData() or "")

    def _has_csrf(self) -> bool:
        return bool(self.session and not self.session.closed and get_cookie_value(self.session, "bili_jct"))

    def _update_action_state(self):
        has_room = self._room_id() is not None
        has_title = bool(self.title_input.text().strip())
        has_area = bool(self._selected_area_id())
        has_csrf = self._has_csrf()
        self.update_title_btn.setEnabled(has_room and has_title and has_csrf)
        self.update_area_btn.setEnabled(has_room and has_area and has_csrf)
        self.start_btn.setEnabled(has_room and has_title and has_area and has_csrf)
        self.stop_btn.setEnabled(has_room and has_csrf)

    def _set_busy(self, busy: bool, message: Optional[str] = None):
        for widget in (
            self.room_id_input,
            self.title_input,
            self.parent_area_combo,
            self.area_combo,
            self.update_title_btn,
            self.update_area_btn,
            self.start_btn,
            self.stop_btn,
        ):
            widget.setEnabled(not busy)
        if message:
            self.set_status(message)
        if not busy:
            self._update_action_state()

    def set_status(self, message: str, error: bool = False):
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #ff7777;" if error else "color: #dddddd;")

    def _save_form_config(self):
        room_id = self._room_id()
        save_config(
            {
                "room_id": room_id if room_id is not None else self.room_id_input.text().strip(),
                "live_title": self.title_input.text().strip(),
                "live_parent_area_id": str(self.parent_area_combo.currentData() or ""),
                "live_area_id": self._selected_area_id(),
            }
        )

    @qasync.asyncSlot()
    async def handle_update_title(self):
        if not self.session:
            return
        room_id = self._room_id()
        title = self.title_input.text().strip()
        if room_id is None or not title:
            self.set_status("房间号和标题不能为空。", error=True)
            return
        self._set_busy(True, "正在更新标题...")
        try:
            await update_room_title(self.session, room_id, title)
            self._save_form_config()
            self.set_status("直播间标题已更新。")
        except Exception as e:
            logger.exception("Failed to update room title")
            self.set_status(f"更新标题失败: {e}", error=True)
        finally:
            self._set_busy(False)

    @qasync.asyncSlot()
    async def handle_update_area(self):
        if not self.session:
            return
        room_id = self._room_id()
        area_id = self._selected_area_id()
        if room_id is None or not area_id:
            self.set_status("房间号和分区不能为空。", error=True)
            return
        self._set_busy(True, "正在更新分区...")
        try:
            await update_room_area(self.session, room_id, area_id)
            self._save_form_config()
            self.set_status("直播间分区已更新。")
        except Exception as e:
            logger.exception("Failed to update room area")
            self.set_status(f"更新分区失败: {e}", error=True)
        finally:
            self._set_busy(False)

    @qasync.asyncSlot()
    async def handle_start_live(self):
        if not self.session:
            return
        room_id = self._room_id()
        title = self.title_input.text().strip()
        area_id = self._selected_area_id()
        if room_id is None or not title or not area_id:
            self.set_status("请填写房间号、标题和分区。", error=True)
            return
        self._set_busy(True, "正在开始直播...")
        try:
            self._save_form_config()
            await update_room_title(self.session, room_id, title)
            await update_room_area(self.session, room_id, area_id)
            version = await get_live_version(self.session)
            result = await start_live(self.session, room_id, area_id, version.curr_version, str(version.build))
            if result.code == 0:
                self.credentials = parse_stream_credentials(result.data)
                self._render_credentials()
                self.set_status("直播已开始，推流凭证已生成。")
                return
            if result.code == 60024:
                self._show_qr_verification(str(result.data.get("qr") or ""))
                self.set_status("本次开播需要扫码验证，完成后请重新点击开始直播。")
                return
            if result.code == 60043:
                uid = get_cookie_value(self.session, "DedeUserID")
                if uid:
                    self._show_text_dialog("人脸认证", format_face_auth_url(uid))
                self.set_status("本次开播需要人脸认证，完成后请重新点击开始直播。", error=True)
                return
            self.set_status(f"开始直播失败: {result.message} ({result.code})", error=True)
        except LiveApiError as e:
            logger.exception("Live API error while starting live")
            self.set_status(str(e), error=True)
        except Exception as e:
            logger.exception("Failed to start live")
            self.set_status(f"开始直播失败: {e}", error=True)
        finally:
            self._set_busy(False)

    @qasync.asyncSlot()
    async def handle_stop_live(self):
        if not self.session:
            return
        room_id = self._room_id()
        if room_id is None:
            self.set_status("房间号无效。", error=True)
            return
        self._set_busy(True, "正在停止直播...")
        try:
            await stop_live(self.session, room_id)
            self.credentials = []
            self._render_credentials()
            self.set_status("直播已停止。")
        except Exception as e:
            logger.exception("Failed to stop live")
            self.set_status(f"停止直播失败: {e}", error=True)
        finally:
            self._set_busy(False)

    def _render_credentials(self):
        while self.credentials_layout.count():
            item = self.credentials_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self.credentials:
            self.empty_credentials_label = QLabel("开播成功后会在这里显示 RTMP/SRT 地址和密钥。")
            self.empty_credentials_label.setWordWrap(True)
            self.credentials_layout.addWidget(self.empty_credentials_label)
            return

        for credential in self.credentials:
            self.credentials_layout.addWidget(self._credential_row(credential))
        self.credentials_layout.addStretch()

    def _credential_row(self, credential: StreamCredential) -> QWidget:
        row = QFrame(self)
        row.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QGridLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(credential.label.upper())
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title, 0, 0, 1, 3)

        address = QLineEdit(credential.address)
        address.setReadOnly(True)
        copy_address = QPushButton("复制地址")
        copy_address.clicked.connect(lambda _checked=False, text=credential.address: self.copy_to_clipboard(text))
        layout.addWidget(QLabel("地址"), 1, 0)
        layout.addWidget(address, 1, 1)
        layout.addWidget(copy_address, 1, 2)

        key = QLineEdit(credential.key)
        key.setReadOnly(True)
        copy_key = QPushButton("复制密钥")
        copy_key.clicked.connect(lambda _checked=False, text=credential.key: self.copy_to_clipboard(text))
        layout.addWidget(QLabel("密钥"), 2, 0)
        layout.addWidget(key, 2, 1)
        layout.addWidget(copy_key, 2, 2)
        return row

    def copy_to_clipboard(self, text: str):
        QApplication.clipboard().setText(text)
        self.set_status("已复制到剪贴板。")

    def _show_qr_verification(self, url: str):
        if not url:
            self._show_text_dialog("验证", "本次开播需要扫码验证，但接口未返回二维码地址。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("开播验证")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("请使用哔哩哔哩 App 扫码完成验证，完成后重新点击开始直播。"))
        bio = self.auth_manager.generate_qr_image(url)
        if bio:
            image = QImage.fromData(bio.getvalue())
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(QPixmap.fromImage(image).scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio))
            layout.addWidget(label)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def _show_text_dialog(self, title: str, text: str):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        copy_btn = box.addButton("复制", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() == copy_btn:
            self.copy_to_clipboard(text)
```

- [ ] **Step 3: Run syntax check for dialog**

Run:

```bash
uv run python -m py_compile src/bilihud/live_control_dialog.py
```

Expected result: command exits with code 0 and prints no errors.

- [ ] **Step 4: Commit Task 3**

Run:

```bash
git add src/bilihud/live_control_dialog.py
git commit -m "feat: add live control dialog"
```

---

### Task 4: Tray Integration

**Files:**
- Modify: `src/bilihud/danmaku_widget.py`

- [ ] **Step 1: Add import**

In `src/bilihud/danmaku_widget.py`, add this import next to the existing local imports:

```python
from .live_control_dialog import LiveControlDialog
```

- [ ] **Step 2: Add tray action**

In `setup_tray_icon()`, after the扫码登录 action is added and before the quit action, insert:

```python
        self.tray_live_control_action = QAction("直播控制", self)
        self.tray_live_control_action.triggered.connect(self.open_live_control)
        tray_menu.addAction(self.tray_live_control_action)
```

The resulting menu order in that section should be:

```python
        self.tray_login_action = QAction("扫码登录", self)
        self.tray_login_action.triggered.connect(self.open_qr_login)
        tray_menu.addAction(self.tray_login_action)

        self.tray_live_control_action = QAction("直播控制", self)
        self.tray_live_control_action.triggered.connect(self.open_live_control)
        tray_menu.addAction(self.tray_live_control_action)
        
        quit_action = QAction("退出程序", self)
```

- [ ] **Step 3: Add `open_live_control()` method**

Add this method immediately before `open_qr_login()`:

```python
    def open_live_control(self):
        """打开直播控制窗口"""
        if not hasattr(self, "_live_control_dialog") or self._live_control_dialog is None:
            self._live_control_dialog = LiveControlDialog(self)
        self._live_control_dialog.set_room_id(self.room_id)
        self._live_control_dialog.show()
        self._live_control_dialog.raise_()
        self._live_control_dialog.activateWindow()
```

- [ ] **Step 4: Run syntax check**

Run:

```bash
uv run python -m py_compile src/bilihud/danmaku_widget.py
```

Expected result: command exits with code 0 and prints no errors.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add src/bilihud/danmaku_widget.py
git commit -m "feat: open live control from tray"
```

---

### Task 5: Full Verification

**Files:**
- Verify all modified source and tests.

- [ ] **Step 1: Run unit tests**

Run:

```bash
uv run pytest tests/test_live_api.py tests/test_auth.py tests/test_utils.py -v
```

Expected result: all tests PASS.

- [ ] **Step 2: Run syntax checks**

Run:

```bash
uv run python -m py_compile \
  src/bilihud/live_api.py \
  src/bilihud/auth.py \
  src/bilihud/danmaku_client.py \
  src/bilihud/live_control_dialog.py \
  src/bilihud/danmaku_widget.py
```

Expected result: command exits with code 0 and prints no errors.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected result: only intended feature files are changed after the last task commit. The untracked `temp/` reference directory may still appear and must not be committed.

- [ ] **Step 4: Manual smoke test in a desktop session**

Run:

```bash
uv run bilihud
```

Expected manual checks:

- Tray menu contains "直播控制".
- Clicking "直播控制" opens the dialog.
- Without valid cookies, mutating actions are disabled and the dialog asks for扫码登录.
- With valid cookies, the area list loads and title/area validation controls the start button.
- Closing the dialog does not quit BiliHUD.

Do not perform a real `Room/startLive` call unless the account is allowed to start a live session and the operator intends to start one.

## Self-Review

- Spec coverage: the plan covers live API, shared auth, dialog UI, tray entry, credential parsing, QR/face verification display, no credential persistence, and tests for pure logic.
- Type consistency: `StreamCredential.label/address/key`, `LiveVersion.curr_version/build`, and `StartLiveResult.code/message/data` are defined in Task 1 and used consistently later.
- Scope: the main overlay remains unchanged except for the tray action and dialog opener.
