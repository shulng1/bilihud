import asyncio
import logging
from typing import Any

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
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("直播控制")
        self.setMinimumSize(520, 540)

        self.auth_manager = AuthManager()
        self.session: aiohttp.ClientSession | None = None
        self.area_list: list[dict[str, Any]] = []
        self.credentials: list[StreamCredential] = []
        self._initial_load_started = False
        self._busy = False

        self._init_ui()
        self._load_config_values()
        self._update_action_state()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        self.status_label = QLabel("打开后会加载登录状态和直播分区。")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #dddddd;")
        main_layout.addWidget(self.status_label)

        form = QFrame(self)
        form.setStyleSheet(
            """
            QFrame {
                background: #2b2b2b;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
            }
            QLabel {
                color: #eeeeee;
                border: none;
            }
            QLineEdit, QComboBox {
                color: #eeeeee;
                background: #1f1f1f;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 5px 7px;
            }
            QPushButton {
                color: #ffffff;
                background: #00a1d6;
                border: none;
                border-radius: 4px;
                padding: 6px 10px;
            }
            QPushButton:disabled {
                color: #888888;
                background: #3a3a3a;
            }
            QPushButton:hover:!disabled {
                background: #00b5e5;
            }
            """
        )
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
        main_layout.addWidget(form)

        credentials_title = QLabel("推流凭证")
        credentials_title.setStyleSheet("font-weight: bold; color: #eeeeee;")
        main_layout.addWidget(credentials_title)

        self.credentials_scroll = QScrollArea(self)
        self.credentials_scroll.setWidgetResizable(True)
        self.credentials_scroll.setStyleSheet(
            """
            QScrollArea {
                background: #1f1f1f;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
            }
            """
        )
        self.credentials_container = QWidget()
        self.credentials_layout = QVBoxLayout(self.credentials_container)
        self.credentials_layout.setContentsMargins(8, 8, 8, 8)
        self.credentials_layout.setSpacing(8)
        self.credentials_scroll.setWidget(self.credentials_container)
        main_layout.addWidget(self.credentials_scroll, 1)

        self._render_credentials()

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        close_row.addWidget(self.close_btn)
        main_layout.addLayout(close_row)

    def _load_config_values(self) -> None:
        config = load_config()
        room_id = config.get("room_id", "")
        self.room_id_input.setText(str(room_id) if room_id else "")
        self.title_input.setText(str(config.get("live_title", "")))

    def set_room_id(self, room_id: int) -> None:
        if room_id > 0:
            self.room_id_input.setText(str(room_id))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_load_started or self.session is None or self.session.closed:
            self._initial_load_started = True
            asyncio.create_task(self.load_initial_state())

    def closeEvent(self, event) -> None:
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())
        self._initial_load_started = False
        super().closeEvent(event)

    async def load_initial_state(self) -> None:
        self._set_busy(True, "正在加载登录状态和直播分区...")
        try:
            self.session, _from_keyring = await self.auth_manager.create_authenticated_session()
            if self._has_csrf():
                self.set_status("登录状态可用。")
            else:
                self.set_status("未找到 CSRF Token，请先通过托盘菜单扫码登录。", error=True)

            self.area_list = await get_area_list(self.session)
            self._populate_parent_areas()
            self._restore_saved_area()
        except Exception as exc:
            logger.exception("Failed to initialize live control dialog")
            self.set_status(f"初始化失败: {exc}", error=True)
        finally:
            self._set_busy(False)

    def _populate_parent_areas(self) -> None:
        self.parent_area_combo.blockSignals(True)
        self.parent_area_combo.clear()
        for parent in self.area_list:
            self.parent_area_combo.addItem(str(parent.get("name") or ""), str(parent.get("id") or ""))
        self.parent_area_combo.blockSignals(False)
        self._on_parent_area_changed()

    def _restore_saved_area(self) -> None:
        config = load_config()
        parent_id = str(config.get("live_parent_area_id", ""))
        area_id = str(config.get("live_area_id", ""))
        if parent_id:
            parent_index = self.parent_area_combo.findData(parent_id)
            if parent_index >= 0:
                self.parent_area_combo.setCurrentIndex(parent_index)
        if area_id:
            area_index = self.area_combo.findData(area_id)
            if area_index >= 0:
                self.area_combo.setCurrentIndex(area_index)

    def _on_parent_area_changed(self) -> None:
        current_parent_id = str(self.parent_area_combo.currentData() or "")
        selected_parent = next(
            (parent for parent in self.area_list if str(parent.get("id") or "") == current_parent_id),
            None,
        )

        self.area_combo.blockSignals(True)
        self.area_combo.clear()
        if selected_parent:
            for area in selected_parent.get("list") or []:
                self.area_combo.addItem(str(area.get("name") or ""), str(area.get("id") or ""))
        self.area_combo.blockSignals(False)
        self._update_action_state()

    def _room_id(self) -> int | None:
        text = self.room_id_input.text().strip()
        if not validate_room_id(text):
            return None
        return int(text)

    def _selected_area_id(self) -> str:
        return str(self.area_combo.currentData() or "")

    def _has_csrf(self) -> bool:
        return bool(self.session and not self.session.closed and get_cookie_value(self.session, "bili_jct"))

    def _update_action_state(self) -> None:
        if self._busy:
            return
        has_room = self._room_id() is not None
        has_title = bool(self.title_input.text().strip())
        has_area = bool(self._selected_area_id())
        has_csrf = self._has_csrf()
        self.update_title_btn.setEnabled(has_room and has_title and has_csrf)
        self.update_area_btn.setEnabled(has_room and has_area and has_csrf)
        self.start_btn.setEnabled(has_room and has_title and has_area and has_csrf)
        self.stop_btn.setEnabled(has_room and has_csrf)

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = busy
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

    def set_status(self, message: str, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #ff7777;" if error else "color: #dddddd;")

    def _save_form_config(self) -> None:
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
    async def handle_update_title(self) -> None:
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
        except Exception as exc:
            logger.exception("Failed to update room title")
            self.set_status(f"更新标题失败: {exc}", error=True)
        finally:
            self._set_busy(False)

    @qasync.asyncSlot()
    async def handle_update_area(self) -> None:
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
        except Exception as exc:
            logger.exception("Failed to update room area")
            self.set_status(f"更新分区失败: {exc}", error=True)
        finally:
            self._set_busy(False)

    @qasync.asyncSlot()
    async def handle_start_live(self) -> None:
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
            self._handle_start_live_result(result.code, result.message, result.data)
        except LiveApiError as exc:
            logger.exception("Live API error while starting live")
            self.set_status(str(exc), error=True)
        except Exception as exc:
            logger.exception("Failed to start live")
            self.set_status(f"开始直播失败: {exc}", error=True)
        finally:
            self._set_busy(False)

    def _handle_start_live_result(self, code: int, message: str, data: dict[str, Any]) -> None:
        if code == 0:
            self.credentials = parse_stream_credentials(data)
            self._render_credentials()
            if self.credentials:
                self.set_status("直播已开始，推流凭证已生成。")
            else:
                self.set_status("直播已开始，但接口未返回可识别的推流凭证。", error=True)
            return

        if code == 60024:
            self._show_qr_verification(self._extract_qr_url(data))
            self.set_status("本次开播需要扫码验证，完成后请重新点击开始直播。", error=True)
            return

        if code == 60043:
            uid = get_cookie_value(self.session, "DedeUserID") if self.session else None
            if uid:
                self._show_text_dialog("人脸认证", format_face_auth_url(uid))
            else:
                self._show_text_dialog("人脸认证", "本次开播需要人脸认证，但当前会话缺少 DedeUserID。")
            self.set_status("本次开播需要人脸认证，完成后请重新点击开始直播。", error=True)
            return

        self.set_status(f"开始直播失败: {message or 'Unknown Error'} ({code})", error=True)

    @staticmethod
    def _extract_qr_url(data: dict[str, Any]) -> str:
        for key in ("qr", "qrcode", "qrcode_url", "url"):
            value = data.get(key)
            if value:
                return str(value)
        return ""

    @qasync.asyncSlot()
    async def handle_stop_live(self) -> None:
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
        except Exception as exc:
            logger.exception("Failed to stop live")
            self.set_status(f"停止直播失败: {exc}", error=True)
        finally:
            self._set_busy(False)

    def _render_credentials(self) -> None:
        while self.credentials_layout.count():
            item = self.credentials_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self.credentials:
            empty_label = QLabel("开播成功后会在这里显示 RTMP/SRT 地址和密钥。")
            empty_label.setWordWrap(True)
            empty_label.setStyleSheet("color: #aaaaaa;")
            self.credentials_layout.addWidget(empty_label)
            self.credentials_layout.addStretch()
            return

        for credential in self.credentials:
            self.credentials_layout.addWidget(self._credential_row(credential))
        self.credentials_layout.addStretch()

    def _credential_row(self, credential: StreamCredential) -> QWidget:
        row = QFrame(self)
        row.setStyleSheet(
            """
            QFrame {
                background: #292929;
                border: 1px solid #3f3f3f;
                border-radius: 6px;
            }
            QLabel {
                color: #eeeeee;
                border: none;
            }
            QLineEdit {
                color: #eeeeee;
                background: #1f1f1f;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 5px 7px;
            }
            QPushButton {
                color: #ffffff;
                background: #555555;
                border: none;
                border-radius: 4px;
                padding: 5px 8px;
            }
            QPushButton:hover {
                background: #666666;
            }
            """
        )
        layout = QGridLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

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
        key.setEchoMode(QLineEdit.EchoMode.Password)
        copy_key = QPushButton("复制密钥")
        copy_key.clicked.connect(lambda _checked=False, text=credential.key: self.copy_to_clipboard(text))
        layout.addWidget(QLabel("密钥"), 2, 0)
        layout.addWidget(key, 2, 1)
        layout.addWidget(copy_key, 2, 2)
        return row

    def copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.set_status("已复制到剪贴板。")

    def _show_qr_verification(self, url: str) -> None:
        if not url:
            self._show_text_dialog("开播验证", "本次开播需要扫码验证，但接口未返回二维码地址。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("开播验证")
        layout = QVBoxLayout(dialog)

        prompt = QLabel("请使用哔哩哔哩 App 扫码完成验证，完成后重新点击开始直播。")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        bio = self.auth_manager.generate_qr_image(url)
        if bio:
            image = QImage.fromData(bio.getvalue())
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    220,
                    220,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            layout.addWidget(label)
        else:
            layout.addWidget(QLabel(url))

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def _show_text_dialog(self, title: str, text: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        copy_btn = box.addButton("复制", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() == copy_btn:
            self.copy_to_clipboard(text)
