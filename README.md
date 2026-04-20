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
- 向量索引采用本地 `LanceDB`

## 1. 环境变量

建议在启动前设置：

```powershell
$env:EMBEDDING_BASE_URL="http://127.0.0.1:1234/V1"
$env:EMBEDDING_MODEL="text-embedding-bge-base-zh"
$env:EMBEDDING_API_KEY="your-local-api-key"
$env:MCP_SEARCH_WORKSPACE_ROOT="D:\\your_repo"
```

可选变量：

```powershell
$env:MCP_SEARCH_INDEX_DIR="D:\\your_repo\\.mcp-index"
$env:MCP_SEARCH_MAX_FILE_BYTES="300000"
$env:MCP_SEARCH_AUTO_REINDEX="true"
$env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS="5"
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

这个启动脚本会自动读取本地 embedding JSON 配置并注入环境变量。

## 5. 接入 Claude Code

示意：

```powershell
claude mcp add local-search -- powershell -File D:\trae_prj\mcp_sd\run-local-mcp-search.ps1 -WorkspaceRoot D:\your_repo
```

## 6. 当前限制

- 代码切块先采用轻量规则，不做完整 AST 级别切块
- 向量索引存在 `.mcp-index\lancedb\`
- 元数据状态存在 `.mcp-index\metadata.json`
- 已支持 `full / incremental / auto` 三种重建模式
- 已支持可选的后台自动增量更新，当前采用轮询方式
- 大仓库下性能还不算最优，后续可继续增加原生文件事件监听和更细粒度切块

