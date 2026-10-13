# 问题自检 · 文档一键转智能自检题库

> 终版 v2（SRS 间隔重复版）· 封版日期 2026-10-12
> 线上地址：https://question-self-check.app.workbuddy.host/

把复习资料（PDF / Word / TXT / MD / 图片 / 粘贴文本）一键转成可自测的智能题库：
AI 自动抽题 → 逐题翻卡自检 → 薄弱点自动汇总 → **间隔重复复习（SRS）**。

---

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
pip install -r requirements.txt

# 2. 启动（serve.py 会读取 PORT 环境变量，默认 8000）
python serve.py
# 或带守护重启：python watchdog.py

# 3. 打开
# http://127.0.0.1:8000/  → 登录页（游客可一键体验，无需注册）
```

> WorkBuddy sites 部署参数：installCmd `pip install -r requirements.txt`，startCmd `python serve.py`。
> ⚠️ 同一 WorkBuddy 工作区多项目发布时注意 appId 会被自动选中覆盖（2026-10-12 踩过：电商小二覆盖过本站，重发即恢复）。

## 功能清单（26 项）

**核心流程**：文档导入（多格式+分片上传+自动重试）→ 本地规则 / LLM 双引擎抽题（按版块并行、无关内容过滤、元问题硬过滤）→ 题库管理（单题编辑/删除/去重/手动添加/题型指定/重新生成答案）→ 题型配置 → 逐题翻卡自检（原文对照 / 上一题回改 / 随机顺序 / 错题本 / 语音输入）→ 统计总结（掌握率+方面分析+薄弱项复制/Markdown 导出）→ 历史记录（文档/会话两级）。

**质量与安全**：答案 0 误差节点裁剪、双端净化、AI 幻觉提示、LLM 外发确认、按用户日额度限流、访问码防护（默认开启，A4）、隐私大白话。

**游客体系**：免注册一键体验（自动载示例）→ 注册后数据自动迁移。

**SRS 间隔重复**：自检结果自动喂卡；熟悉 1→3→7→14→30 天递增，不熟悉当天重练；任务栏「🔁 今日复习」+ 到期徽标。

## 目录结构

```
├── index.html        # 主应用（单文件前端，~170KB）
├── login.html        # 登录/游客入口
├── serve.py          # 启动器（读 PORT，兼容 WorkBuddy sites）
├── watchdog.py       # 进程守护（子进程退出自动重启）
├── start.py          # 备用启动脚本
├── requirements.txt  # 依赖
├── assets/cat/       # 猫咪吉祥物素材
├── backend/
│   ├── main.py       # FastAPI 全部业务（抽取/历史/访客/迁移/额度/SRS）
│   ├── db.py         # SQLite 持久化层（USE_SQLITE=0 切内存模式）
│   └── __init__.py
├── tests/            # 7 套自动化（297 项）+ 陷阱文档 E2E
├── docs/             # 产品/验收/测评报告
└── tools/            # 开发期一次性脚本存档
```

## 测试

```bash
# 七套回归（297 项）
python tests/check_frontend.py
node tests/test_frontend_logic.js
python tests/test_srs.py          # SRS 双模式（内存+SQLite）
python tests/test_live_server.py  # 真 HTTP 全链路 E2E
python tests/test_access_code.py
python tests/test_history.py
python tests/test_history_sqlite.py

# 真实 LLM 陷阱测试（需后端配置模型 Key）
python tests/trap_e2e.py
```

## 质量档案

- 自动化回归 **297/297 全绿**
- 真实 LLM 陷阱实测：59 题**零幻觉**（8 类陷阱文档，详见 docs/）
- 综合评分 **8.6/10**（产品专家评审）

## 部署必读（批次一安全止血 A3/A4）

1. **访问码默认关闭**（B4，2026-10-13：平台将自部署到用户自有服务器）：需要门槛时设置环境变量 `ACCESS_CODE=<你的码>` 即开启；设为空字符串或未设置 = 关闭。
2. **LLM 凭据只走环境变量**：源码与前端不再内置/接收任何 API Key，部署时设置 `LLM_API_KEY`（SiliconFlow 或其他 OpenAI 兼容端点），可选 `LLM_BASE_URL` / `LLM_TEXT_MODEL` / `LLM_VISION_MODEL`。云沙箱无法注入环境变量时，可把 Key 写入 `backend/llm_key.txt`（该目录公网不可读）。未配置 Key 时 LLM/OCR 调用会明确报错，不会静默降级。
3. 数据存 SQLite（`backend/data.db`，首次运行自动建表），按用户隔离
4. 文档仅用于生成题目，不用于模型训练；LLM 识别可换成自有模型
5. 清空入口：任务栏 → 历史记录 → 清空
