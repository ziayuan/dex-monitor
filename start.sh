#!/bin/bash

# 切换到脚本所在目录
cd "$(dirname "$0")"

echo "🚀 正在启动 Dex Monitor..."

# 1. 检查并创建虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 未发现 venv，正在创建并初始化虚拟环境..."
    python3 -m venv venv
fi

# 2. 激活虚拟环境
source venv/bin/activate

# 3. 安装依赖
echo "🔄 正在检查并安装 Python 依赖库..."
pip install -r requirements.txt -q

# 4. 检查配置文件
if [ ! -f ".env" ]; then
    echo "⚠️ 警告: 配置文件 .env 不存在！"
    echo "已为你复制了一份 .env 文件。请务必编辑它并填入你的 TELEGRAM_BOT_TOKEN。"
    cp .env.example .env
    exit 1
fi

if [ ! -f "config.json" ]; then
    cp config.example.json config.json
fi

# 5. 确保 logs 目录存在
mkdir -p logs

# 6. 清理旧进程 (防止重复一直发消息)
echo "🧹 清理可能存在的旧进程..."
pkill -f "python apps/funding_rate_monitor.py"

# 7. 使用 nohup 在后台运行监控程序
echo "▶️ 正在后台启动资金费率监控..."
nohup python apps/funding_rate_monitor.py > logs/startup.log 2>&1 &

echo "------------------------------------------------------"
echo "✅ 启动成功！监控程序已在后台隐蔽运行。"
echo "📄 查看应用产生的日志请使用: tail -f logs/funding_rate_monitor.log"
echo "🛑 如果想停止程序，请运行: ./stop.sh"
echo "------------------------------------------------------"
