# 企业问题自检平台 · Question Self-Check

> FastAPI + 单文件前端 · 企业级问题全生命周期管理（发现 → 整改 → 闭环）
> 线上演示：https://question-self-check.app.workbuddy.host/

面向企业内审 / 合规 / 质量场景的**问题自检工作台**：把散落在各处的风险点沉淀成结构化问题库，
按「待整改 → 整改中 → 已闭环」推进闭环，并自动汇总成风险报告。

平台同时内置**文档一键转自检题库**模块（`quiz.html`），把复习资料（PDF / Word / TXT / MD / 图片）
转为可自测题库，支持间隔重复（SRS）复习。

---

## 功能一览

### 主工作台（`index.html`）
| 模块 | 说明 |
| --- | --- |
| 首页仪表盘 | 风险闭环率 / 问题总数 / 高风险未闭环 / 进行中任务四级统计卡，今日自检任务表，高频问题分类环图（点击扇区联动筛选） |
| 自检任务 | 任务全生命周期管理：**行内状态下拉**（待开始 / 进行中 / 已完成，改完即存）+ 每行**刷新**按钮 + 新建 / 删除任务 |
| 问题库 | 多维筛选（类别 / 风险等级 / 整改状态，全部实时联动）+ 行内推进（开始整改 → 确认闭环）+ 示例数据一键导入 |
| 风险报告 | 问题总数 / 高风险未闭环 / 闭环率 / 进行中任务看板，类目分布条形图，高风险清单，支持打印与导出 Markdown |
| 系统设置 | 站点信息、数据管理（导出 / 清空） |
| AI 助手「小暹」 | 悬浮智能体，仅回答本平台问题 + 兜底闲聊（服务端人设边界约束） |

### 文档抽题（`quiz.html`）
文档导入 → 本地规则 / LLM 双引擎抽题 → 题库管理 → 逐题翻卡自检 → 掌握率与薄弱项汇总 →
**SRS 间隔重复**（熟悉 1→3→7→14→30 天递增）。

### 登录（`login.html`）
左侧品牌区 + 右侧四种登录方式：**账号密码 / 手机验证码 / 微信扫码 / 支付宝扫码**（演示环境为仿真二维码与模拟授权）。

## 技术栈

- **后端**：FastAPI + Uvicorn，SQLite 持久化（`backend/db.py`），SSE 流式输出
- **前端**：单文件 HTML + 原生 JS（无构建步骤），全局事件委托统一分发
- **LLM**：OpenAI 兼容端点（SiliconFlow / DeepSeek 等），文本与视觉模型可分别配置
- **安全**：静态白名单托管（源码 / 数据库 / 密钥后缀一律 404）、访问码开关、按用户日额度限流、密钥只走环境变量

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2. 启动（serve.py 读取 PORT 环境变量，默认 8000）
python serve.py
# 或带守护重启：python watchdog.py

# 3. 打开
# http://127.0.0.1:8000/  → 登录页（账号密码登录可直接注册，无需邮箱）
```

### 配置 LLM（可选，不配则抽题走本地规则引擎）

```bash
export LLM_API_KEY=<你的 Key>          # 必需
export LLM_BASE_URL=https://api.siliconflow.cn/v1
export LLM_TEXT_MODEL=Qwen/Qwen3-8B
export LLM_VISION_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct
```

云环境无法注入环境变量时，可把 Key 写入 `backend/llm_key.txt`（该文件已被 `.gitignore` 排除，且静态托管不可读）。

## 目录结构

```
├── index.html        # 企业工作台（仪表盘 / 自检任务 / 问题库 / 风险报告 / 系统设置）
├── login.html        # 登录页（四种登录方式）
├── quiz.html         # 文档抽题 + 自检 + SRS 复习
├── serve.py          # 生产启动入口（读 PORT，兼容云沙箱）
├── watchdog.py       # 进程守护（子进程退出自动重启）
├── start.py          # 本地开发启动（自动挑端口 + 开浏览器）
├── requirements.txt
├── assets/cat/       # 吉祥物「小暹」精灵图
├── backend/
│   ├── main.py       # FastAPI 全部业务（鉴权 / 抽题 / 历史 / 额度 / AI 助手 / 静态托管）
│   └── db.py         # SQLite 持久化层
├── tests/            # 自动化回归 + 陷阱文档 E2E
└── docs/             # 产品 / 验收 / 测评报告
```

## 测试

```bash
python tests/check_frontend.py     # 前端静态与安全断言（90 项）
node   tests/test_frontend_logic.js # 前端纯逻辑切片测试（27 项）
python tests/test_access_code.py   # 访问码开关行为
python tests/test_history.py       # 历史记录（内存 + SQLite 双模式）
python tests/test_live_server.py   # 真 HTTP 全链路 E2E
python tests/trap_e2e.py           # 真实 LLM 陷阱文档测试（需配置 Key）
```

## 部署说明

1. **访问码默认关闭**：需要门槛时设置 `ACCESS_CODE=<你的码>` 即开启；未设置或设为空 = 关闭。
2. **密钥只走环境变量**（或 `backend/llm_key.txt` 兜底），源码与前端不内置任何 Key。
3. 数据存 `backend/data.db`（首次运行自动建表），按用户隔离；该文件不入库。
4. 单端口部署：服务监听 `PORT` 环境变量并绑定 `0.0.0.0`，可直接跑在任意容器 / 云主机上。

## 许可

个人作品集项目，欢迎交流。
