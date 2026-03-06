# KwaiGrow Assistant

> 原项目内部名：`ks-ai-auto-commenter`  
> 对外开源建议名：**KwaiGrow Assistant（快手增长助手）**

一个基于 `Python + Playwright + OpenAI-compatible API + SQLite` 的快手内容互动自动化工具。  
目标是帮助你在可控节奏下做“搜索 → 阅读 → 评论 → 去重记录”的标准化运营流程。

## 1. 功能概览

- AI 扩词：按方向词扩展搜索关键词，支持历史去重。
- 帖子检索：按关键词抓取候选帖子，支持排序/时间范围。
- 上下文理解：抓取帖子摘要和热评摘要。
- AI 生成评论：根据规则生成候选评论并过滤。
- 自动发送与确认：提交评论并做结果确认/重试。
- 去重与限流：按帖子ID/URL/标题哈希去重，支持每日上限和每轮上限。
- 可视化控制台：开始/停止、实时日志、评论记录、关键词历史、AI连接测试。

## 2. 适用场景

- 个人账号内容互动运营（轻量养号/活跃度维持）。
- 运营同学做关键词主题触达测试。
- 需要“可重复、可回溯”的评论流程自动化。
- 在明确策略和节奏控制前提下，做小规模自动化试验。

## 3. 项目结构（当前主实现）

```text
ks-ai-auto-commenter/
├─ main.py                          # CLI 主入口
├─ dashboard.py                     # 控制台入口
├─ src/app/
│  ├─ main.py                       # 程序启动与参数解析
│  ├─ orchestrator.py               # 核心流程编排
│  ├─ dashboard.py                  # Flask 控制台 + API
│  ├─ config.py                     # 配置模型与加载
│  ├─ ai/
│  │  ├─ openai_client.py           # 模型请求封装
│  │  ├─ keyword_expander.py        # 扩词
│  │  └─ comment_engine.py          # 评论判定/生成/筛选
│  ├─ browser/
│  │  └─ kuaishou_client.py         # 快手页面自动化
│  └─ storage/dedup_store.py        # SQLite 去重与记录
├─ config/
│  ├─ selectors/kuaishou.yaml       # 选择器配置
│  └─ kuaishou.yaml.example         # 开源示例配置（脱敏）
├─ logs/                            # 本地运行日志（不提交）
├─ data/                            # 本地数据库与浏览器数据（不提交）
└─ docs/
   └─ KWAIGROW_GUIDE.md             # 详细说明文档
```

> 备注：仓库内还有历史 `app/` 目录，可视为旧实现兼容层；当前运行主路径是 `src/app/`。

## 4. 快速开始

### 4.1 安装

```bash
cd ks-ai-auto-commenter
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
python -m playwright install chromium
```

### 4.2 配置（推荐）

```bash
cp config/kuaishou.yaml.example config.realrun.local.yaml
```

填写你自己的：
- `openai.base_url`
- `openai.api_key`
- `openai.model_id`
- `topics.direction_keywords`
- `comment_rules.requirements`

### 4.3 命令行运行

```bash
source .venv/bin/activate
python main.py --config ./config.realrun.local.yaml --once
```

### 4.4 控制台运行

```bash
source .venv/bin/activate
python dashboard.py --config ./config.realrun.local.yaml --host 127.0.0.1 --port 8091
```

浏览器访问：`http://127.0.0.1:8091`

## 5. 近期结构/逻辑优化

- 关键词与方向词标准化去重（去空白、去重复），减少重复搜索与噪音关键词。
- 控制台增加“设置”折叠面板，核心开始/停止按钮上置。
- 新增 AI 测试连接接口，失败返回可读错误与堆栈，便于排障。
- 告警逻辑优化：避免旧日志导致“AI配置异常”误报。

## 6. 开源前注意

- 不要提交任何真实 `api_key`、本地 `config.realrun*.yaml`、`logs/`、`data/`。
- 本仓库已提供 `.gitignore` 和 `config/kuaishou.yaml.example`。
- 建议在开源仓库启用基础安全扫描（secret scan / dependency scan）。

## 7. 免责声明

本项目仅用于技术学习与合规运营自动化实践。请遵守平台规则、法律法规及账号使用规范。