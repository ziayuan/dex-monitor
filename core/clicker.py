"""Mouse click automation with safety features."""
import json
import math
import time
from pathlib import Path

import pyautogui

# Disable PyAutoGUI's built-in pause for maximum speed
pyautogui.PAUSE = 0

# Constants
CLICK_DELAY = 0.0001
MOVEMENT_THRESHOLD = 800  # pixels/second

# Module state
_var_pos: pyautogui.Point | None = None
_lig_pos: pyautogui.Point | None = None
_last_mouse_pos: tuple | None = None
_last_mouse_time: float | None = None
_interrupted: bool = False
_on_interrupt_callback = None


def set_interrupt_callback(callback):
    """Set a callback to be called when mouse movement interrupts clicks."""
    global _on_interrupt_callback
    _on_interrupt_callback = callback


def is_interrupted() -> bool:
    """Check if clicking was interrupted."""
    return _interrupted


def reset_interrupt():
    """Reset the interrupt flag."""
    global _interrupted
    _interrupted = False


def check_mouse_movement() -> bool:
    """Detect fast mouse movement as an interrupt signal.
    
    Returns True if movement speed exceeds threshold.
    """
    global _last_mouse_pos, _last_mouse_time, _interrupted

    try:
        current_pos = pyautogui.position()
        current_time = time.time()

        if _last_mouse_pos is None or _last_mouse_time is None:
            _last_mouse_pos = (current_pos.x, current_pos.y)
            _last_mouse_time = current_time
            return False

        dx = current_pos.x - _last_mouse_pos[0]
        dy = current_pos.y - _last_mouse_pos[1]
        distance = math.sqrt(dx * dx + dy * dy)
        time_delta = current_time - _last_mouse_time

        if time_delta < 0.01:
            return False

        speed = distance / time_delta
        _last_mouse_pos = (current_pos.x, current_pos.y)
        _last_mouse_time = current_time

        if speed > MOVEMENT_THRESHOLD:
            print(f"\n⚠️ 快速移动检测 (速度: {speed:.0f} px/s) -> 触发急停")
            _interrupted = True
            if _on_interrupt_callback:
                _on_interrupt_callback()
            return True
        return False
    except Exception:
        return False


def load_coordinates(path: Path | None = None) -> bool:
    """Load click coordinates from JSON file.
    
    Returns True if coordinates loaded successfully.
    """
    global _var_pos, _lig_pos

    if path is None:
        path = Path(__file__).parent.parent / "coordinates.json"

    try:
        with path.open("r") as f:
            coords = json.load(f)
        _var_pos = pyautogui.Point(coords["var_pos"]["x"], coords["var_pos"]["y"])
        _lig_pos = pyautogui.Point(coords["lig_pos"]["x"], coords["lig_pos"]["y"])
        print(f"✅ 坐标已加载: Var({_var_pos.x},{_var_pos.y}) Lig({_lig_pos.x},{_lig_pos.y})")
        return True
    except FileNotFoundError:
        print("❌ 未找到 coordinates.json")
        return False
    except Exception as e:
        print(f"❌ 坐标加载失败: {e}")
        return False


def save_coordinates(var_pos: tuple, lig_pos: tuple, path: Path | None = None):
    """Save click coordinates to JSON file."""
    if path is None:
        path = Path(__file__).parent.parent / "coordinates.json"

    coords = {
        "var_pos": {"x": var_pos[0], "y": var_pos[1]},
        "lig_pos": {"x": lig_pos[0], "y": lig_pos[1]},
    }
    with path.open("w") as f:
        json.dump(coords, f)
    print("💾 坐标已保存到 coordinates.json")


def get_coordinates() -> tuple:
    """Get current loaded coordinates."""
    return _var_pos, _lig_pos


def perform_clicks(double_click: bool = True, stabilization_ms: int = 20):
    """Execute double-click on both positions.
    
    Args:
        double_click: Use double-click instead of single click
        stabilization_ms: Wait time after moving cursor before clicking
    """
    global _interrupted

    if _interrupted or not _var_pos or not _lig_pos:
        return

    if check_mouse_movement():
        return

    # Click Var position
    pyautogui.moveTo(_var_pos.x, _var_pos.y)
    time.sleep(stabilization_ms / 1000)
    if double_click:
        pyautogui.doubleClick(_var_pos.x, _var_pos.y, interval=0.03)
    else:
        pyautogui.click(_var_pos.x, _var_pos.y)

    time.sleep(CLICK_DELAY)

    # Click Lig position
    pyautogui.moveTo(_lig_pos.x, _lig_pos.y)
    time.sleep(stabilization_ms / 1000)
    if double_click:
        pyautogui.doubleClick(_lig_pos.x, _lig_pos.y, interval=0.03)
    else:
        pyautogui.click(_lig_pos.x, _lig_pos.y)


def record_position(name: str, delay: int = 3) -> tuple:
    """Interactively record a mouse position.
    
    Args:
        name: Name to display for this position
        delay: Seconds to wait before recording
    
    Returns:
        Tuple of (x, y) coordinates
    """
    print(f"请把鼠标移动到{name}上，{delay}秒后自动记录坐标...")
    time.sleep(delay)
    pos = pyautogui.position()
    print(f"✅ {name}坐标记录为: ({pos.x}, {pos.y})")
    return (pos.x, pos.y)
