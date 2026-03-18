# 🎯 Variational & Lighter 套利交易与套保监控工具

自动化套利下单与数据监控工具库，支持 **Variational** 和 **Lighter** 交易所的价差监控、自动对冲点击，以及 **Lighter** 与 **TradeXYZ(Hyperliquid)** 的资金费率与价格区间监控。

## 📁 核心功能模块

### 1. 资金费率与价格监控 (Funding & Price Monitor)
- **脚本:** `apps/funding_rate_monitor.py`
- **功能:**
  - 监控 Lighter vs TradeXYZ 的资金费率差（支持 `EURUSD` / `USDJPY` 等品种）。
  - 监控 Lighter 上特定合约的价格范围。
  - 支持 Telegram Bot 实时报警与动态下发配置指令 (`/addprice`, `/setdir`, `/list` 等)。

### 2. 跨所自动对冲点击 (Hedge Clicker)
- **脚本:** `apps/lig_hedge_window.py`
- **功能:**
  - 浮窗界面 (PyQt5) 实时显示 Variational 和 Lighter 的买卖盘数据与跨所价差。
  - 自动基于坐标点击网页下单按钮，实现屏幕级的物理级自动交易。
  - 支持设置安全点击间隔、滑点容忍、最大执行次数。

### 3. 持仓不平衡监控 (Position Monitor)
- **脚本:** `apps/lig_position_monitor.py`  
- **功能:**
  - 监控长线双边持仓，如一方未成交或偏差过大，通过 Telegram 进行告警。

### 4. 数据采集与分发 (Spread Recorder)
- **脚本:** `apps/lig_spread_recorder.py`
- **功能:**
  - 后台录制与收集实时 Orderbook 和盘口报价历史。
  - 提供 WebSocket 接口供 Frontend 或其他 Dashboard 使用。

## 🚀 快速开始

### 1. 配置环境

本工具推荐使用 Python 3.10+。

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 初始化配置文件

工具的核心配置分离在环境变量 `.env` 和 `config.json` 中。
系统默认忽略了敏感文件，避免上传到远程仓库。

```bash
# 1. 复制配置示例文件
cp .env.example .env
cp config.example.json config.json

# 2. 填写你自己的 API Key / Telegram Token / 代理地址
vim .env
```

`.env` 示例需要包含：
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `LIGHTER_PRIVATE_KEY` / `VAR_LIG_TOKEN`
- `var_http_proxy` / `FUNDING_PROXY` (如果在国内需要走代理连 Hyperliquid)

### 3. 运行任意模块

**启动资金费率监控:**
```bash
python apps/funding_rate_monitor.py
```

**启动自动点击对冲浮窗:**
```bash
python apps/lig_hedge_window.py
```

## ⚠️ 安全说明 (Security Notice)

1. **绝对不要**将 `.env` 或 `config.json` 提交到公开的 Git 仓库（它们已在 `.gitignore` 中默认被忽略）。
2. 在使用 `lig_hedge_window.py` 自动点击下单前，务必先校准屏幕坐标 (`coordinates.json`)，测试期间请将交易额设为最小值。
3. `funding_rate_monitor.py` 对于 TradeXYZ (Hyperliquid) 接口使用 `curl_cffi` 模拟指纹，如果请求偶尔超时请检查 `.env` 中的代理连通性。

## 🔧 开发与扩展

增加新策略或监控指标：
1. 核心的底层行情 Client 封装在 `exchanges/` 和 `core/data_feeds.py`。
2. 配置参数集中由 `core/config.py` 解析。
3. 可根据需求在 `apps/` 目录下新增独立脚本。
