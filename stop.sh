#!/bin/bash
echo "🛑 正在停止资金费率监控程序..."
pkill -f "python apps/funding_rate_monitor.py"
echo "✅ 程序已完全停止。"
