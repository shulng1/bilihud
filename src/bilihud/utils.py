import os
import json
from pathlib import Path
from typing import Dict, Any

def get_config_path() -> Path:
    """获取配置文件路径 (遵循XDG规范)"""
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    config_dir = Path(xdg_config_home) / 'bilihud'
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / 'config.json'

def load_config() -> Dict[str, Any]:
    """加载配置"""
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to load config: {e}")
        return {}

def save_config(data: Dict[str, Any]) -> bool:
    """保存配置"""
    try:
        config_path = get_config_path()
        
        # 读取现有配置以进行合并，防止覆盖其他配置项
        current_config = load_config()
        current_config.update(data)
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Failed to save config: {e}")
        return False

def validate_room_id(room_id_str: str) -> bool:
    """
    验证直播间ID是否有效

    Args:
        room_id_str: 直播间ID字符串

    Returns:
        bool: 如果有效返回True，否则返回False
    """
    try:
        room_id = int(room_id_str)
        return room_id > 0
    except ValueError:
        return False


def format_danmaku_message(danmaku_msg) -> str:
    """
    格式化弹幕消息用于显示

    Args:
        danmaku_msg: 弹幕消息对象

    Returns:
        str: 格式化后的弹幕消息
    """
    return f"{danmaku_msg.uname}: {danmaku_msg.msg}"
