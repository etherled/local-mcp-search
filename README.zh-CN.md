# local-mcp-search

English README: [README.md](/D:/trae_prj/mcp_sd/README.md)

本项目提供一个本地 `STDIO MCP Server`，面向 `Codex`、`Claude Code` 以及其他支持 MCP 的 coding agent。
核心思路很简单：用少量本地 CPU/GPU 资源做检索、rerank 和上下文压缩，让远端模型少做盲扫，把预算更多花在推理和改代码上。

## 为什么值得用

如果没有本地检索层，coding agent 很容易出现这几类浪费：

- 搜得太宽
- 读太多文件
- 把昂贵上下文浪费在低价值文本上
- 多花几轮才补齐正确实现上下文

`local-search` 把这些工作下沉到本地基础设施：

- 用本地 `ripgrep` 做精确检索
- 用本地 embedding 做语义召回
- 用本地 reranker 做排序
- 用更紧凑的 span/context 打包方式喂给 agent
- 通过 MCP tools 和 resources 直接接入 agent 工作流

目标不是“多几个工具”，而是更好的 agent 经济性：

- 不牺牲任务成功率
- 降低无效 token 消耗
- 减少不必要轮次
- 更快拿到正确上下文

## 已测效果

当前 benchmark 已经说明这套思路有实际价值，但不同 agent 的收益结构并不完全相同。

| Agent | 样本 | 成功率 | 当前主要收益 |
| --- | --- | --- | --- |
| `Claude` | `4 tasks`，`baseline vs local-search` | `4/4 -> 4/4` | cost `-31.92%`，turns `-33.33%`，latency `-15.59%` |
| `Codex` | `4 tasks`，`baseline vs local-search` | `4/4 -> 4/4` | token `-21.94%`，latency `-3.76%` |

当前更准确的解读是：

- 对 `Claude`，最强信号是 `成功率 + 计费 + 耗时 + 轮次`
- 对 `Codex`，最强信号是 `成功率 + 耗时 + token`
- 收益指标会受 agent 和 provider 路由影响，不宜强行统一成单一口径

因此，当前公开表述比较稳妥的说法应是：

- `local-search` 已经在受控 benchmark 中体现出可测量、可落地的效率收益
- 当前典型收益大致在 `15%~30%` 区间，但具体提升哪一项指标，要看 agent 本身

## 原理

整体设计并不复杂：

1. 用少量本地资源构建并查询本地索引
2. 在 agent 真正读文件前，先召回并 rerank 候选文件或代码片段
3. 只把最相关的上下文发给远端模型

本质上，这是把重复的远端上下文浪费，换成一个更轻量的本地检索层。

## 当前定位

- `alpha / 0.1.x`
- `Windows-first`
- 面向 `Codex`、`Claude Code` 和 MCP 风格的 coding workflow
- 当前优先按本地 `llama-server` 部署优化

## 你能得到什么

- 足够快的本地精确检索，替代大范围盲扫
- 在远端模型消耗上下文前，先做本地语义召回和 rerank
- 更紧凑的文件/span 打包，减少“读了很多但没读到关键点”
- 一个统一的 `cpx` 入口，负责启动、reindex、MCP 注册和会话恢复
- 更贴近真实 coding-agent 工作流的 MCP tools 与 resources

## 已包含的 MCP Tools

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

## 当前状态

截至 `2026-05-09`：

- embedding 和 reranking 已切到本地 `llama-server` 部署
- `cpx` 空参默认启动 `Codex`，并按当前 workspace 恢复最近会话
- `cpx -Claude` 会恢复当前 workspace 的最近 Claude 会话
- `doctor` 可直接检查模型健康状态和 MCP 注册状态
- `repo://overview`、`repo://dependency-summary`、`repo://changes` 已可作为稳定 resource 使用
- 仓库已内置 `baseline x local-search x Codex x Claude` 的自动 benchmark harness

当前最强的受控 benchmark 证据主要来自 `Claude + Xiaomi Mimo 2.5 Pro`，以及一组更早验证过兼容性的 `Codex` 路由。

## 适合场景

- 中大型仓库
- 需要频繁恢复工作上下文
- 需要同时查代码和知识库
- 依赖 `Codex` / `Claude Code` 的日常开发流

## 不适合场景

- 很小的仓库
- 几乎不需要语义搜索
- 不愿维护本地模型
- 追求跨平台零配置

## 快速开始

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

先设置模型路径：

```powershell
$env:LOCAL_SEARCH_LLAMA_SERVER="D:\path\to\llama-server.exe"
$env:LOCAL_SEARCH_EMBED_GGUF="D:\models\bge-base-zh.f16.gguf"
$env:LOCAL_SEARCH_RERANK_GGUF="D:\models\bge-reranker-v2-m3-Q8_0.gguf"
```

然后做最小 smoke test：

```powershell
python -m local_mcp_search.cli reindex --mode auto
python -m local_mcp_search.cli status
python -m local_mcp_search.cli doctor
codex mcp get local-search --json
claude mcp get local-search
```

统一入口：

```powershell
cpx
cpx -Claude
```

## 环境变量

通常不需要手工设置 embedding / reranker 相关环境变量；`launcher` 会自动注入。

项目级搜索/索引配置可放在工作区根目录 `.local-search.json`。
示例见 [.local-search.example.json](/D:/trae_prj/mcp_sd/.local-search.example.json:1)。

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
- 项目级 `.local-search.json` 会覆盖部分索引/搜索行为，适合每个仓库单独调优
- `reindex` 时 embedding 采用批量请求，避免单次请求过大
- 如果切换了 embedding 模型或维度，建议先跑 `reindex full`
- 设置 `MCP_SEARCH_QUERY_DEBUG=true` 后，`code_exact_search` / `code_semantic_search` / `kb_search` / `code_context_pack` 会在返回 JSON 中附带 `debug` 字段
- 如果模型路径未配置，`launcher` 会在启动阶段直接失败，而不是隐式回退到作者本机路径

`.local-search.json` 当前支持：

- `ignore_dirs`: 额外忽略的目录名，按路径段匹配
- `doc_dirs`: 文档目录白名单；这些目录下的文件会优先按知识库文档处理
- `max_file_bytes`: 项目级大文件上限，会覆盖 `MCP_SEARCH_MAX_FILE_BYTES`
- `languages`: 语言白名单；设置后只索引这些语言的代码文件，文档文件不受影响

示例：

```json
{
  "ignore_dirs": [".openhands", "vendor", "tmp"],
  "doc_dirs": ["docs", "notes", "runbooks"],
  "max_file_bytes": 200000,
  "languages": ["python", "typescript", "javascript"]
}
```

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
codex mcp get local-search --json
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

如果 `Codex` / `Claude Code` 里仍然报 MCP 启动失败，或 `change_context` / `repo://changes` 这类工具异常：

- 先确认 `codex mcp get local-search --json` / `claude mcp get local-search` 指向的是当前工作区的 `.mcp-index/_mcp_server_wrapper.py`
- 如果路径已经对了，但客户端会话还是沿用旧连接，重启一次客户端会话再试
- 再用 `python -m local_mcp_search.cli doctor` 看 `codex_mcp_matches_workspace`、`embedding`、`reranker` 状态

典型异常含义：

- `codex_mcp_matches_workspace=false`：客户端还连着旧工作区或旧 wrapper
- `change_context` 在部分 Windows MCP 宿主里仍可能快速返回 timeout：当前版本会优先避免长时间挂死；这时先用 `repo://changes`，或缩小 `max_results` 后重试
- 旧版本里 `change_context` 的 `Invalid argument` / 长时间卡死，通常和宿主进程 `stderr` 句柄兼容有关；升级到当前版本并重启会话即可
- `code_context_pack` / `code_semantic_search` 报连接失败：embedding 或 reranker 本地服务没起来，不是 MCP 注册问题

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

如果客户端更偏好 MCP resources，也可以直接读这些稳定资源：

- `repo://overview`
- `repo://dependency-summary`
- `repo://changes`

它们分别对应：

- 仓库结构与入口概览
- 依赖与构建配置摘要
- 当前变更文件与压缩上下文

这类 resource 更适合：

- `Claude Code` 一类更自然消费 resource 的客户端
- 反复读取、内容相对稳定的信息
- resume / review / setup 这类不需要参数化搜索的场景

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

`change_context` 现在会额外给出：

- 新增 / 修改 / 删除 / 重命名 / 未跟踪 的变更类型
- `docs` / `code` / `config` / `tests` / `high_attention` 分组
- 基于文件类型和 diff 规模的风险等级
- git `numstat` 摘要，方便快速判断改动体量

## Benchmark

仓库已经内置自动 benchmark harness，可对比 `Claude` / `Codex` 在 `baseline` 与 `local-search` 下的表现。

当前受控结果已经说明这套方案有实际价值：

| Agent | 样本 | 成功率 | 当前主要结果 |
| --- | --- | --- | --- |
| `Claude` | `4 tasks` | `4/4 -> 4/4` | cost `-31.92%`，turns `-33.33%`，latency `-15.59%` |
| `Codex` | `4 tasks` | `4/4 -> 4/4` | token `-21.94%`，latency `-3.76%` |

这也是当前 benchmark 最核心的结论：

- 对 `Claude`，价值重点是保持成功率，同时降低计费、耗时和轮次
- 对 `Codex`，最清晰的信号是 token 下降，同时有轻微提速
- benchmark 结论应保持 agent-specific，而不是强行把所有客户端都压成同一指标口径

当前已验证的样本来源：

- `Claude + Xiaomi Mimo 2.5 Pro`
- 一组更早验证过兼容性的 `Codex` 路由

当前边界也很明确：

- 部分第三方或非官方兼容链路虽然能用于交互对话，但会在 `Codex exec`、`Responses API` 或 structured output 路径上单独失败
- 这类链路不应直接拿来做正式的 `Codex` benchmark 结论

复现入口：

- [scripts/run_benchmark.py](/D:/trae_prj/mcp_sd/scripts/run_benchmark.py:1)

直接运行默认矩阵：

```powershell
python .\scripts\run_benchmark.py
```

更细的 benchmark 工作流、结果结构和兼容性说明见：

- [benchmark/README.md](/D:/trae_prj/mcp_sd/benchmark/README.md:1)

## 已知限制

- 当前仍是 `Windows-first`
- 当前依赖本地 `llama-server` 部署与模型文件
- 第三方 `Codex` 转发链路在 `schema` / `response_format` 路径上可能不兼容；并非所有 OpenAI 兼容接口都能直接用于当前 `Codex CLI`
- 如果 `Codex` 交互式对话正常，但 benchmark 的 `schema` 模式失败，不代表 benchmark 存在并发问题，更常见原因是供应商对 `exec` / `Responses API` / structured output 链路兼容不完整
- `change_context` 在部分 Windows MCP 宿主里仍可能走快速 timeout 回退；稳定 resume / review 场景可优先用 `repo://changes`

## 为什么不用 grep / Read

- `code_exact_search` 比手工 `rg` 更贴近 agent 的结构化调用
- `file_outline` 先看结构，能避免盲读整文件
- `open_spans` 只取必要片段，减少无效上下文
- `code_context_pack` 能把搜索、读片段和压缩合成一次

## 备注

- 当前实现已经从“远端 OpenAI 兼容 embedding/reranker JSON 配置启动”切换为“本地 llama-server 模型部署 + Python launcher 启动”
- 旧的 `run-local-mcp-search.ps1` / `use-local-mcp-search.ps1` 已不再是主入口
- 当前推荐入口是 `cpx.ps1` 或 `python -m local_mcp_search.launcher`
