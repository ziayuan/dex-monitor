# 🎯 Variational-Paradex 套利交易工具

自动化套利下单工具，支持 Variational 和 Paradex 交易所的价差监控与自动点击。

## 📁 目录结构

```
├── core/                   # 共享核心模块
│   ├── config.py          # 统一配置管理
│   ├── clicker.py         # 点击逻辑 + 安全检测
│   ├── data_feeds.py      # Var HTTP + Paradex WS 数据源
│   └── alerts.py          # Telegram 报警
│
├── strategies/            # 策略模块
│   ├── base.py            # 策略基类
│   ├── var_paradex.py     # Var-Paradex 价差策略
│   └── var_backpack.py    # [预留] Var-Backpack 策略
│
├── apps/                  # 应用入口
│   ├── floating_window.py # GUI 浮窗监控
│   ├── simple_click.py    # 控制台点击工具
│   └── position_monitor.py # Telegram 持仓监控
│
├── var_extension/         # Chrome 扩展 (流量镜像)
├── config.json            # 用户配置
├── coordinates.json       # 点击坐标
└── dashboard_paradex.html # 价差可视化面板
```

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
编辑 `config.json`：
- `var_cookie`: Variational 的有效 Cookie
- `telegram_bot_token` / `telegram_chat_id`: Telegram 报警配置
- `pairs`: 监控的交易对列表

### 3. 运行

**浮窗监控（推荐）**
```bash
python apps/floating_window.py
```
- 实时显示 Var/Para 盘口价格和价差
- 支持自动点击下单
- 始终置顶，可拖动

**控制台点击工具**
```bash
python apps/simple_click.py
```
- 模式 1/2：自动循环下单
- 模式 3：手动回车触发
- 模式 4：监听价差自动点击

**持仓监控 (Telegram)**
```bash
python apps/position_monitor.py
```
- 持仓不平衡报警
- Telegram `/status` 查询
- `/get` 静音

## ⚙️ 参数配置

| 参数 | 位置 | 说明 |
|------|------|------|
| 价差阈值 | 浮窗 UI | 触发点击的最小价差 |
| Max Clicks | 浮窗 UI | 最大点击次数 |
| Cd (Cooldown) | 浮窗 UI | 两次点击的冷却时间 |
| Cf (Confirm) | 浮窗 UI | 连续确认次数 |

## ⚠️ 注意事项

1. 首次运行需记录按钮坐标 (模式 2)
2. 快速晃动鼠标可紧急中断
3. 需配合代理使用 (`var_http_proxy`)
4. macOS 完成时会播放提示音

## 🔧 扩展开发

添加新策略：
1. 在 `strategies/` 创建新文件
2. 继承 `BaseStrategy` 基类
3. 实现 `calculate_signal()` 方法
