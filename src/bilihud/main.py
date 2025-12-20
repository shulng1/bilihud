# -*- coding: utf-8 -*-
import sys
import os
import sys

# Force X11 backend (xcb) on Linux to support XShape click-through
# This must be set before QApplication is instantiated.
if sys.platform == "linux":
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtWidgets import QApplication
import qasync
import asyncio
from .danmaku_widget import DanmakuWidget


async def main(app, room_id: int = 7450109):
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    # 创建弹幕窗口
    danmaku_widget = DanmakuWidget(room_id)

    # 显示窗口
    danmaku_widget.show()

    await app_close_event.wait()

def entry_point():
    import argparse
    from PyQt6.QtCore import Qt

    parser = argparse.ArgumentParser(description="B站弹幕阅读器")
    parser.add_argument("--room-id", "-r", type=int, default=7450109, help="直播间ID")
    args = parser.parse_args()

    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"

    if hasattr(Qt.HighDpiScaleFactorRoundingPolicy, 'PassThrough'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    
    app = QApplication(sys.argv)
    
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main(app, args.room_id))
    finally:
        loop.close()

if __name__ == "__main__":
    entry_point()
