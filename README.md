# local-mcp-search

本项目提供一个最小可用的本地 `STDIO MCP Server`，用于给 `Codex` / `Claude Code` 暴露以下能力：

- `code_exact_search`
- `symbol_search`
- `code_semantic_search`
- `kb_search`
- `repo_overview`
- `open_spans`
- `index_status`
- `reindex`

第一版定位：

- `code_exact_search` 基于 `ripgrep`
- `code_semantic_search` / `kb_search` 基于本地 OpenAI 兼容 embedding 接口
- 可选使用 `qwen3-reranker-8b` 对语义召回结果做二阶段重排
- 向量索引采用本地 `LanceDB`

## 1. 环境变量

建议在启动前设置：

```powershell
$env:EMBEDDING_BASE_URL="http://127.0.0.1:1234/V1"
$env:EMBEDDING_MODEL="text-embedding-bge-base-zh"
$env:EMBEDDING_API_KEY="your-local-api-key"
$env:MCP_SEARCH_WORKSPACE_ROOT="D:\\your_repo"
```

启用 reranker 时额外设置：

```powershell
$env:MCP_SEARCH_RERANKER_ENABLED="true"
$env:RERANKER_BASE_URL="https://api.lingyaai.cn/v1"
$env:RERANKER_MODEL="qwen3-reranker-8b"
$env:RERANKER_API_KEY="your-reranker-api-key"
```

可选变量：

```powershell
$env:MCP_SEARCH_INDEX_DIR="D:\\your_repo\\.mcp-index"
$env:MCP_SEARCH_MAX_FILE_BYTES="300000"
$env:MCP_SEARCH_AUTO_REINDEX="true"
$env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS="5"
$env:MCP_SEARCH_RERANKER_CANDIDATE_MULTIPLIER="6"
$env:MCP_SEARCH_RERANKER_MAX_CANDIDATES="80"
$env:RERANKER_TIMEOUT_SECONDS="30"
```

如果已经有 `LanceDB` 旧表或索引目录，`reindex` 会覆盖重建 `chunks` 表。

## 2. 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## 3. 本地调试

先重建索引：

```powershell
python -m local_mcp_search.cli reindex
```

显式全量重建：

```powershell
python -m local_mcp_search.cli reindex --mode full
```

只处理变更文件：

```powershell
python -m local_mcp_search.cli reindex --mode incremental
```

说明：

- `auto` 为默认值
- 在 Git 仓库里，`auto` 会结合 `last_indexed_commit`、已暂存变更、未暂存变更、未跟踪文件来判断受影响路径
- 在非 Git 目录里，`auto` 会退化为基于文件 `mtime/size` 的 manifest 对比

查看状态：

```powershell
python -m local_mcp_search.cli status
```

直接启动 stdio server：

```powershell
python -m local_mcp_search
```

或者直接读取你的本地模型 JSON 配置启动：

```powershell
.\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo
```

默认会读取：

- embedding 配置：`C:\Users\yyyx\Documents\models-setting\my-embd-bge-zh.json`
- reranker 配置：`C:\Users\yyyx\Documents\models-setting\qwen3-reranker_lingya.json`

如果暂时不想使用 reranker：

```powershell
.\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo -DisableReranker
```

启用后台自动增量更新：

```powershell
.\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo -AutoReindex -AutoReindexIntervalSeconds 5
```

也可以用 MCP Inspector：

```powershell
mcp dev src/local_mcp_search/server.py
```

推荐使用顺序：

- 先用 `repo_overview` 快速看项目结构、入口文件和文档入口
- 查具体函数、类、接口、类型定义时优先用 `symbol_search`
- 查精确文本出现位置时用 `code_exact_search`
- 查相似实现或相关逻辑时用 `code_semantic_search`
- 查设计方案、计划、ADR、说明文档时用 `kb_search`
- 搜到候选后，用 `open_spans` 拉精确上下文，不要直接展开整文件

## 4. 接入 Codex

示意：

```powershell
codex mcp add local-search -- powershell -File D:\trae_prj\mcp_sd\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo
```

这个启动脚本会自动读取本地 embedding JSON 和 reranker JSON 配置并注入环境变量。

### 任意目录下的便捷方式（推荐）

如果你希望在任何目录打开终端都能快速把 MCP 指向“当前项目”，并先刷新索引，使用：

```powershell
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot (Get-Location).Path
```

这个脚本会自动做三件事：

1. 使用你的本地模型配置完成 `reindex`
2. 更新 `codex mcp` 中的 `local-search` 指向当前项目
3. 可选直接启动 `codex`

如果你已经把 `cpx` 别名写入 PowerShell profile，日常可以直接用：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Mode auto
```

最短启动流程一般是这条：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Launch
```

如果当前终端已经在项目根目录，直接执行：

```powershell
cpx -Launch
```

常用参数：

```powershell
# 首次或大改后强制全量重建，再启动 codex
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot D:\trae_prj\myagent -ReindexMode full -LaunchCodex

# 日常增量刷新
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot D:\trae_prj\myagent -ReindexMode auto

# 指定不同模型配置
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot D:\trae_prj\myagent -ModelConfigPath C:\path\to\your-model.json

# 指定不同 reranker 配置
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot D:\trae_prj\myagent -RerankerConfigPath C:\path\to\your-reranker.json

# 关闭 reranker，只使用 LanceDB 向量排序
D:\trae_prj\mcp_sd\use-local-mcp-search.ps1 -ProjectRoot D:\trae_prj\myagent -DisableReranker
```

验证 Codex 是否接上：

```powershell
codex mcp list
codex mcp get local-search
```

进入 Codex 后，可以直接要求它使用本 MCP：

```text
先用 local-search 看一下项目结构。
```

```text
调用 index_status 看看 local-search 的状态，确认 reranker_enabled 和 reranker_model。
```

```text
用 code_semantic_search 找登录鉴权相关实现，再用 open_spans 打开关键片段。
```

正常情况下，`index_status` 应该能看到：

```text
reranker_enabled: true
reranker_model: qwen3-reranker-8b
```

## 5. 接入 Claude Code

示意：

```powershell
claude mcp add local-search -- powershell -File D:\trae_prj\mcp_sd\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo
```

针对具体项目，例如：

```powershell
claude mcp add local-search -- powershell -File D:\trae_prj\mcp_sd\run-local-mcp-search.ps1 -WorkspaceRoot D:\trae_prj\myagent
```

验证 Claude Code 是否接上：

```powershell
claude mcp list
claude mcp get local-search
```

然后在目标项目里启动 Claude Code：

```powershell
cd D:\trae_prj\myagent
claude
```

进入 Claude Code 后，可以这样要求它使用 MCP：

```text
先用 local-search 的 repo_overview 看项目结构，然后用 code_semantic_search 找相关实现。
```

```text
用 kb_search 查项目文档里的部署说明，再用 open_spans 打开最相关片段。
```

如果 reranker API 临时不可用，可以重新注册为关闭 reranker：

```powershell
claude mcp remove local-search
claude mcp add local-search -- powershell -File D:\trae_prj\mcp_sd\run-local-mcp-search.ps1 -WorkspaceRoot D:\trae_prj\myagent -DisableReranker
```

## 6. 当前限制

- 代码切块先采用轻量规则，不做完整 AST 级别切块
- 向量索引存在 `.mcp-index\lancedb\`
- 元数据状态存在 `.mcp-index\metadata.json`
- 已支持 `full / incremental / auto` 三种重建模式
- 已支持可选的后台自动增量更新，当前采用轮询方式
- reranker 接口不可用时会自动回退到 LanceDB 原始向量排序
- 大仓库下性能还不算最优，后续可继续增加原生文件事件监听和更细粒度切块

