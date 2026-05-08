# local-mcp-search

本项目提供一个本地 `STDIO MCP Server`，给 `Codex` / `Claude Code` 提供代码库与知识库检索能力。

当前公开版本建议定位为：

- `alpha / 0.1.x`
- `Windows-first`
- 面向 `Codex` / `Claude Code` 的本地检索型 MCP

当前优先支持：

- `Windows 10/11 x64`
- PowerShell 工作流
- 本地 `llama-server` 部署

当前提供的 MCP tools：

- `code_exact_search`
- `symbol_search`
- `code_semantic_search`
- `code_context_pack`
- `kb_search`
- `doc_answer_context`
- `file_outline`
- `symbol_context`
- `change_context`
- `dependency_overview`
- `repo_overview`
- `open_spans`
- `index_status`
- `reindex`

当前实现重点：

- 精确检索基于本地 `ripgrep`
- 语义检索基于本地 embedding
- reranker 基于本地 llama-server 部署
- 向量索引使用本地 `LanceDB`
- `cpx` 统一负责启动本地模型、刷新索引、注册 MCP、恢复最近会话

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## 本地模型部署

当前默认走本地 `llama-server`，由 [launcher.py](/D:/trae_prj/mcp_sd/src/local_mcp_search/launcher.py:21) 自动拉起两个服务：

- embedding: `bge-base-zh`
- reranker: `bge-reranker-v2-m3`

`launcher` 默认从以下环境变量读取模型路径；如果不设置，则会使用：

- `llama-server` 作为可执行文件名
- 空的 GGUF 路径，启动时直接报错并提示补齐

同时支持私有本地回退配置，不进 git：

- 工作区级：`<repo>/.local-search.env`
- 用户级：`%USERPROFILE%/.local-mcp-search.env`

读取优先级：

- CLI 参数
- 环境变量
- 工作区私有配置
- 用户级私有配置
- 安全默认值

建议先设置：

```powershell
$env:LOCAL_SEARCH_LLAMA_SERVER="D:\path\to\llama-server.exe"
$env:LOCAL_SEARCH_EMBED_GGUF="D:\models\bge-base-zh.f16.gguf"
$env:LOCAL_SEARCH_RERANK_GGUF="D:\models\bge-reranker-v2-m3-Q8_0.gguf"
```

或者写入私有配置文件：

```dotenv
LOCAL_SEARCH_LLAMA_SERVER=D:\path\to\llama-server.exe
LOCAL_SEARCH_EMBED_GGUF=D:\models\bge-base-zh.f16.gguf
LOCAL_SEARCH_RERANK_GGUF=D:\models\bge-reranker-v2-m3-Q8_0.gguf
```

默认端口：

```text
embedding port: 8887
reranker port: 8888
```

`cpx` / `python -m local_mcp_search.launcher` 会先探测端口：

- 如果端口上已有健康服务，直接复用
- 如果端口被占用但接口不健康，直接报错，不会擅自杀进程
- 如果服务未启动，会自动启动本地 `llama-server`

日志默认写到：

```text
%TEMP%\llama-logs\
```

## 快速验证

建议公开用户按下面顺序做最小 smoke test：

1. 准备模型路径

```powershell
$env:LOCAL_SEARCH_LLAMA_SERVER="D:\path\to\llama-server.exe"
$env:LOCAL_SEARCH_EMBED_GGUF="D:\models\bge-base-zh.f16.gguf"
$env:LOCAL_SEARCH_RERANK_GGUF="D:\models\bge-reranker-v2-m3-Q8_0.gguf"
```

2. 刷新索引

```powershell
python -m local_mcp_search.cli reindex --mode auto
```

3. 查看状态

```powershell
python -m local_mcp_search.cli status
```

4. 启动并恢复最近 Codex 会话

```powershell
cpx
```

5. 启动并恢复最近 Claude 会话

```powershell
cpx -Claude
```

6. 检查 MCP 是否已注册

```powershell
codex mcp get local-search
claude mcp get local-search
```

7. 运行诊断

```powershell
python -m local_mcp_search.cli doctor
```

## 环境变量

通常不需要手工设置 embedding / reranker 相关环境变量；`launcher` 会自动注入。

如果你要手工运行 `python -m local_mcp_search`，至少需要：

```powershell
$env:MCP_SEARCH_WORKSPACE_ROOT="D:\your_repo"
$env:EMBEDDING_BASE_URL="http://127.0.0.1:8887/v1"
$env:EMBEDDING_MODEL="bge-base-zh"
$env:EMBEDDING_API_KEY=""
$env:MCP_SEARCH_RERANKER_ENABLED="true"
$env:RERANKER_BASE_URL="http://127.0.0.1:8888"
$env:RERANKER_MODEL="bge-reranker-v2-m3"
$env:RERANKER_API_KEY=""
```

常用可选变量：

```powershell
$env:MCP_SEARCH_INDEX_DIR="D:\your_repo\.mcp-index"
$env:MCP_SEARCH_MAX_FILE_BYTES="300000"
$env:MCP_SEARCH_AUTO_REINDEX="false"
$env:MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS="5"
$env:MCP_SEARCH_RERANKER_CANDIDATE_MULTIPLIER="6"
$env:MCP_SEARCH_RERANKER_MAX_CANDIDATES="80"
$env:MCP_SEARCH_RERANKER_CACHE_ENABLED="true"
$env:MCP_SEARCH_RERANKER_CACHE_MAX_ENTRIES="5000"
$env:MCP_SEARCH_CONTEXT_PACK_MAX_CHARS="20000"
$env:MCP_SEARCH_QUERY_DEBUG="false"
$env:EMBEDDING_TIMEOUT_SECONDS="10"
$env:RERANKER_TIMEOUT_SECONDS="30"
$env:MCP_SEARCH_CODE_CHUNK_LINES="120"
$env:MCP_SEARCH_KB_CHUNK_CHARS="1600"
```

说明：

- 默认 `workspace root` 为当前目录；若当前目录在 git 仓库内，会自动提升到 git 根目录
- 默认索引目录为 `<workspace>\.mcp-index`
- `reindex` 时 embedding 采用批量请求，避免单次请求过大
- 如果切换了 embedding 模型或维度，建议先跑 `reindex full`
- 设置 `MCP_SEARCH_QUERY_DEBUG=true` 后，`code_exact_search` / `code_semantic_search` / `kb_search` / `code_context_pack` 会在返回 JSON 中附带 `debug` 字段
- 如果模型路径未配置，`launcher` 会在启动阶段直接失败，而不是隐式回退到作者本机路径

## CLI

刷新索引：

```powershell
python -m local_mcp_search.cli reindex --mode auto
```

强制全量重建：

```powershell
python -m local_mcp_search.cli reindex --mode full
```

只做增量：

```powershell
python -m local_mcp_search.cli reindex --mode incremental
```

查看状态：

```powershell
python -m local_mcp_search.cli status
```

快速生成上下文包：

```powershell
python -m local_mcp_search.cli context-pack "登录鉴权相关实现" --max-results 6 --max-chars 12000
```

打开查询调试输出：

```powershell
$env:MCP_SEARCH_QUERY_DEBUG="true"
python -m local_mcp_search.cli context-pack "登录鉴权相关实现" --max-results 6 --max-chars 12000
```

开启后，返回结果会额外包含：

- 精确搜索命中了多少结果
- 语义召回请求了多少候选、实际返回多少候选
- reranker 是否参与、参与了多少候选重排
- `code_context_pack` 最终裁剪了多少字符

## 直接启动 MCP server

如果你已经自己准备好了环境变量，可以直接启动：

```powershell
python -m local_mcp_search
```

更常用的是通过 launcher 启动：

```powershell
python -m local_mcp_search.launcher
```

它会：

1. 确保本地 embedding / reranker 服务可用
2. 注入当前 workspace 的环境变量
3. 执行 `reindex`
4. 更新 `local-search` MCP 配置
5. 默认启动 `Codex`，并恢复最近会话

## cpx 统一入口

[cpx.ps1](/D:/trae_prj/mcp_sd/cpx.ps1:1) 是 PowerShell 包装入口，默认行为等价于：

```powershell
python -m local_mcp_search.launcher --client codex
```

也就是说，直接执行：

```powershell
cpx
```

会自动：

1. 识别目标 workspace
2. 拉起或复用本地 llama-server
3. 刷新索引
4. 注册 `local-search`
5. 启动 `Codex`
6. 恢复该 workspace 最近会话；如果没有历史则新开

如果你希望在任意目录直接用 `cpx`，可以在 PowerShell profile 里放一个薄包装：

```powershell
function cpx {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    & "D:\trae_prj\mcp_sd\cpx.ps1" @Args
}
```

## cpx 常用示例

当前目录启动并恢复最近 Codex 会话：

```powershell
cpx
```

显式启动 Codex：

```powershell
cpx -Codex
```

显式启动 Claude Code：

```powershell
cpx -Claude
```

指定项目目录：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Codex
```

恢复最近 Claude 会话：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Claude
```

忽略历史直接新开：

```powershell
cpx -Codex -Fresh
cpx -Claude -Fresh
```

手工选择历史会话：

```powershell
cpx -Codex -Pick
cpx -Claude -Pick
```

从最近会话 fork：

```powershell
cpx -Codex -Fork
cpx -Claude -Fork
```

强制全量 reindex：

```powershell
cpx -Codex -ReindexMode full
```

只更新 MCP，不启动客户端：

```powershell
python -m local_mcp_search.launcher --client none
```

关闭 reranker：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Claude -DisableReranker
```

同时注册 Claude MCP：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -Codex -RegisterClaude
```

写出 Claude 项目级 `.mcp.json`：

```powershell
cpx -ProjectRoot D:\trae_prj\myagent -WriteClaudeProjectConfig
```

说明：

- `cpx` 空参默认启动 `Codex`
- `-Claude` 会启动 Claude Code，并恢复该项目最近 Claude 会话
- `-Fork` 对 Codex 使用 `codex fork <session_id>`，对 Claude 使用 `claude --resume <session_id> --fork-session`

## MCP 注册

`launcher` 会自动更新 MCP 配置。

Codex 当前注册方式等价于：

```powershell
codex mcp add local-search -- C:\Program Files\Python311\python.exe D:\your_repo\.mcp-index\_mcp_server_wrapper.py
```

Claude Code 当前注册方式等价于：

```powershell
claude mcp add local-search C:\Program Files\Python311\python.exe D:\your_repo\.mcp-index\_mcp_server_wrapper.py
```

这个 wrapper 会写到：

```text
<workspace>\.mcp-index\_mcp_server_wrapper.py
```

它会在子进程里补齐：

- `PYTHONPATH/src`
- `MCP_SEARCH_WORKSPACE_ROOT`
- embedding / reranker 端点

## 验证 MCP

Codex：

```powershell
codex mcp list
codex mcp get local-search
```

Claude：

```powershell
claude mcp list
claude mcp get local-search
```

也可以直接做健康检查：

```powershell
python -m local_mcp_search.cli status
```

正常情况下，`index_status` 应该能看到：

```text
reranker_enabled: true
reranker_model: bge-reranker-v2-m3
embedding_model: bge-base-zh
health.status: healthy
```

## 在 Codex / Claude 里如何用

进入客户端后，可以直接这样提示：

```text
先用 local-search 看一下项目结构。
```

```text
调用 index_status 看看 local-search 的状态，确认 reranker_enabled 和 reranker_model。
```

```text
用 code_exact_search 找某个具体函数名，再用 open_spans 打开关键片段。
```

```text
用 code_context_pack 看“登录鉴权相关实现”，再基于返回片段继续修改。
```

```text
用 kb_search 查部署说明，再用 open_spans 打开最相关片段。
```

## 工具使用顺序

推荐顺序：

- 先用 `repo_overview` 看项目结构
- 查具体符号优先用 `symbol_search`
- 查具体字符串优先用 `code_exact_search`
- 找相似实现或模式再用 `code_semantic_search`
- 做实现或调试时，优先用 `code_context_pack`
- 打开文件前先用 `file_outline`
- 精读具体片段时用 `open_spans`
- 修改某个函数/类前先用 `symbol_context`
- 看未提交变更时用 `change_context`
- 看依赖和构建方式时用 `dependency_overview`
- 检查索引和后端健康时用 `index_status`

## 适合场景

- 中大型仓库
- 需要频繁恢复工作上下文
- 需要同时查代码和知识库
- 依赖 Codex / Claude Code 的日常开发流

## 不适合场景

- 很小的仓库
- 几乎不需要语义搜索
- 不愿维护本地模型
- 追求跨平台零配置

## 为什么不用 grep / Read

- `code_exact_search` 比手工 `rg` 更贴近 agent 的结构化调用
- `file_outline` 先看结构，能避免盲读整文件
- `open_spans` 只取必要片段，减少无效上下文
- `code_context_pack` 能把搜索、读片段和压缩合成一次

## 备注

- 当前实现已经从“远端 OpenAI 兼容 embedding/reranker JSON 配置启动”切换为“本地 llama-server 模型部署 + Python launcher 启动”
- 旧的 `run-local-mcp-search.ps1` / `use-local-mcp-search.ps1` 已不再是主入口
- 当前推荐入口是 `cpx.ps1` 或 `python -m local_mcp_search.launcher`
