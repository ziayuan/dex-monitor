"""Refactored Simple Click Tool.

Uses core modules for shared functionality.
~150 lines (down from 342)
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import websockets

from core.config import load_config
from core.clicker import (
    load_coordinates, save_coordinates, perform_clicks, 
    check_mouse_movement, is_interrupted, reset_interrupt,
    record_position, get_coordinates
)


# Configuration
CFG = load_config()
ORDER_COUNT = 220
INTERVAL = 5.0
INTERVAL_JITTER = 2.0
USE_DOUBLE_CLICK = True

# Mode 4 defaults
MODE4_SYMBOL = "BTC-USD"
MODE4_TARGET = "spread_a"  # spread_a, spread_b
MODE4_THRESHOLD = 5.0
MODE4_MAX_CLICKS = 38


def print_banner():
    print("\n" + "=" * 50)
    print("🎯 欢迎使用套利下单工具！")
    print("=" * 50)
    print("\n请选择运行模式：")
    print("  1. 使用保存的坐标，自动循环下单")
    print("  2. 重新记录坐标，自动循环下单")
    print("  3. 使用保存的坐标，手动按回车同时下单一次")
    print("  4. 监听价差自动点击")
    print()


def mode_manual(var_pos, lig_pos):
    """Mode 3: Manual click on Enter."""
    print("\n👉 手动模式：每按一次回车会同时点击两边的下单按钮，输入 q 后回车可退出")
    try:
        while True:
            user_input = input("按回车执行下单，输入 q 后回车退出: ").strip().lower()
            if user_input == "q":
                print("👋 已退出手动模式。")
                break
            perform_clicks()
            if is_interrupted():
                print("⚠️ 程序被中断。")
                break
            print("✅ 已执行一次双边下单。")
    except KeyboardInterrupt:
        print("\n\n⚠️ 收到中断信号，正在退出...")


async def mode_ws_trigger():
    """Mode 4: WebSocket triggered clicks."""
    ws_url = f"ws://localhost:{CFG.local_ws_port}"
    op_str = ">="
    click_count = 0
    wait_seconds = max(0.1, INTERVAL + INTERVAL_JITTER)
    
    print(f"🚀 模式4启动，订阅 {ws_url}")
    print(f"   监控: {MODE4_SYMBOL}")
    print(f"   规则: {MODE4_TARGET} {op_str} {MODE4_THRESHOLD}")
    print(f"   ⚠️ 限制: 最大点击 {MODE4_MAX_CLICKS} 次")
    
    last_action = 0
    
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    pairs = data.get("pairs", [])
                    for p in pairs:
                        if p.get("symbol", "").upper() != MODE4_SYMBOL:
                            continue
                        
                        spread_a = p.get("spread_a", 0)
                        spread_b = p.get("spread_b", 0)
                        
                        val = spread_a if MODE4_TARGET == "spread_a" else spread_b
                        triggered = val >= MODE4_THRESHOLD
                        
                        now = time.time()
                        if triggered and now - last_action >= wait_seconds:
                            if click_count >= MODE4_MAX_CLICKS:
                                print(f"🛑 已达到最大点击次数 ({MODE4_MAX_CLICKS})，停止下单。")
                                return
                            
                            perform_clicks()
                            click_count += 1
                            print(f"⚡ 触发规则: {val:.4f} {op_str} {MODE4_THRESHOLD}, 已执行 ({click_count}/{MODE4_MAX_CLICKS})")
                            
                            if click_count >= MODE4_MAX_CLICKS:
                                print(f"🛑 已达到最大点击次数，Mode 4 结束。")
                                return
                            
                            last_action = time.time()
                            
                            # Check for interrupt during cooldown
                            start_cd = time.time()
                            while time.time() - start_cd < wait_seconds:
                                if check_mouse_movement():
                                    print("⚠️ 检测到快速鼠标移动，退出模式4")
                                    return
                                time.sleep(0.1)
        except Exception as e:
            print(f"[WS Error] {e}, 2秒后重连")
            await asyncio.sleep(2)


def mode_auto_loop(order_count: int, var_pos, lig_pos):
    """Mode 1/2: Auto loop with interval."""
    try:
        for i in range(order_count):
            if is_interrupted():
                print(f"\n⏹️ 用户中断，已执行 {i}/{order_count} 次下单")
                break
            print(f"第 {i+1}/{order_count} 次下单...")
            perform_clicks()
            if is_interrupted():
                break
            
            wait_seconds = max(0.1, INTERVAL + INTERVAL_JITTER)
            print(f"⏱️  等待 {wait_seconds:.2f} 秒...")
            
            start_time = time.time()
            while time.time() - start_time < wait_seconds:
                if is_interrupted():
                    break
                check_mouse_movement()
                if is_interrupted():
                    break
                time.sleep(0.1)
        
        if not is_interrupted():
            print("✅ 所有下单完成。")
            os.system("afplay /System/Library/Sounds/Glass.aiff")
        else:
            print("⚠️ 程序被中断。")
    except KeyboardInterrupt:
        print("\n\n⚠️ 收到中断信号...")
        print("正在安全退出...")


def main():
    print_banner()
    
    while True:
        choice = input("请输入选择 (1/2/3/4): ").strip()
        if choice in {"1", "2", "3", "4"}:
            break
        print("❌ 无效选择，请输入 1、2、3 或 4")
    
    print(f"\n✅ 已选择模式 {choice}\n")
    
    use_saved = choice in {"1", "3", "4"}
    
    if use_saved:
        if not load_coordinates():
            print("⚠️  无法加载保存的坐标，将重新记录坐标...")
            use_saved = False
    
    if not use_saved:
        var_pos = record_position("Variational的下单按钮")
        lig_pos = record_position("Lighter的下单按钮")
        save_coordinates(var_pos, lig_pos)
        load_coordinates()
    
    var_pos, lig_pos = get_coordinates()
    
    print("\n📋 下单配置:")
    mode_desc = {
        "1": "自动循环（使用保存坐标）",
        "2": "自动循环（重新记录坐标）",
        "3": "手动回车触发",
        "4": "监听价差自动点击"
    }
    print(f"   - 运行模式: {mode_desc[choice]}")
    print(f"   - 使用双击: {'是' if USE_DOUBLE_CLICK else '否'}")
    print("\n💡 提示: 按Ctrl+C可随时中断程序")
    print("💡 快速晃动鼠标也可中断程序")
    
    if choice == "3":
        mode_manual(var_pos, lig_pos)
    elif choice == "4":
        input("\n👉 确认好页面，按回车开始监听...")
        asyncio.run(mode_ws_trigger())
    else:
        input("\n👉 确认好页面，按回车开始执行下单...")
        mode_auto_loop(ORDER_COUNT, var_pos, lig_pos)


if __name__ == "__main__":
    main()
