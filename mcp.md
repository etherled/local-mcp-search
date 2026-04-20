# 面向 Codex / Claude Code 的本地向量检索 MCP 方案

## 1. 目标

本方案用于构建一个运行在本机的 MCP Server，对外暴露适合代码助手调用的检索工具，服务于 `Codex`、`Claude Code` 等支持 MCP 的客户端。

核心目标：

- 基于本地向量模型和本地索引，提供项目代码库检索、项目知识库检索、模糊查找。
- 让代码助手先检索、再读取、再编辑，而不是依赖大上下文盲读整个项目。
- 提升定位速度、提高回答和改动的命中率。
- 通过“少量高相关结果返回”降低上下文 token 消耗。
- 同时兼容 `Codex` 与 `Claude Code` 的使用习惯。

非目标：

- 不尝试替代 IDE 自带的跳转、语法高亮、LSP。
- 不做通用搜索引擎，不追求全盘互联网检索。
- 不把整个仓库全文直接交给模型。

## 2. 结论先行

这个 MCP 方案对 `Codex` 和 `Claude Code` 都有实际增强作用，尤其适合以下任务：

- “某个功能大概在哪”
- “项目里有没有类似实现”
- “这个错误日志或配置项在哪里出现过”
- “知识库里有没有相关设计说明”
- “先找候选片段，再精确打开上下文”

如果工具设计正确，它通常也能减少 token 成本。节省 token 的关键不在于“用了向量库”，而在于：

- 把大范围扫描放在本地执行
- 只返回少量候选结果
- 把“检索”与“读取全文”拆成两步
- 让模型只拿需要的片段，而不是整文件或整篇文档

## 3. 使用场景

### 3.1 代码库检索

适合：

- 查找函数、类、接口、配置项、错误文案、路径名
- 找“语义上相似”的实现
- 在大型仓库中快速定位修改点

不适合只靠向量：

- 精确符号名查找
- 报错文本、日志片段、SQL 关键字、配置键
- 文件路径、环境变量名、接口路由字符串

因此代码库检索必须采用混合检索，而不是纯向量检索。

### 3.2 项目知识库检索

适合：

- README、ADR、设计文档、运维手册、FAQ、接口规范、测试说明
- 查业务背景、设计约束、已有决策

知识库更适合语义检索，但仍建议保留关键词检索能力。

### 3.3 模糊查找

适合：

- 用户只记得大概名字
- 同义表达、近义描述
- 不确定文件路径或模块归属

模糊查找既可以来自 BM25/全文索引，也可以来自向量召回后的 rerank。

## 4. 总体设计

推荐使用本机 `STDIO MCP Server` 作为第一版落地方式。

原因：

- 最容易接入 `Codex` 和 `Claude Code`
- 与本地向量模型、本地仓库、本地索引天然一致
- 无需先处理鉴权、远程网络暴露、服务发现
- 调试成本低，便于快速迭代工具描述和输出格式

总体架构：

```text
Codex / Claude Code
        |
        v
     MCP Server
        |
        +--------------------+
        |                    |
        v                    v
  Retrieval Orchestrator   Resource Layer
        |
        +--------------------+--------------------+
        |                    |                    |
        v                    v                    v
 Exact/Fuzzy Search     Semantic Search       Span Loader
 (rg/BM25)              (Embedding + ANN)     (按路径/行号回取)
        |
        v
   Index / Metadata Store
        |
        +--------------------+--------------------+
        |                    |                    |
        v                    v                    v
    Repo Chunks         KB Chunks            Symbol Index
```

## 5. 工具设计原则

MCP 工具不要设计成一个“大而全 search”。应拆成语义明确的工具，让模型更容易选择正确工具。

设计原则：

- 精确搜索和语义搜索分开
- 检索和回取上下文分开
- 代码库与知识库分开
- 结果默认短小，必要时再二次展开
- 每条结果必须可追踪到文件、符号和行号

## 6. 推荐的 MCP Tools

### 6.1 `code_exact_search`

用途：

- 精确或近似匹配代码中的符号、字符串、报错文本、配置键、路径片段

适用场景：

- “找 `FeatureFlagX` 在哪里使用”
- “这个报错字符串在哪个模块抛出”
- “看看 `payment_timeout_ms` 配置项都在哪出现”

建议输入：

```json
{
  "query": "payment_timeout_ms",
  "repo": "current",
  "include_globs": ["src/**", "config/**"],
  "exclude_globs": ["node_modules/**", "dist/**"],
  "max_results": 10
}
```

建议输出：

```json
{
  "results": [
    {
      "path": "src/config/payment.ts",
      "line_start": 12,
      "line_end": 16,
      "symbol": "paymentConfig",
      "snippet": "payment_timeout_ms: 5000,",
      "score": 0.98,
      "why_matched": "exact string match"
    }
  ]
}
```

底层建议：

- 优先 `ripgrep`
- 可选叠加 trigram / BM25 排序
- 返回短片段而不是整文件

### 6.2 `code_semantic_search`

用途：

- 基于向量检索与 rerank 查找“语义相似”的代码实现

适用场景：

- “项目里有没有类似的重试逻辑”
- “找一下创建用户后异步发通知的实现”
- “这个服务层的缓存更新模式在哪里还用过”

建议输入：

```json
{
  "query": "find examples of retry with exponential backoff around external API calls",
  "repo": "current",
  "language": ["ts", "js"],
  "max_results": 8
}
```

建议输出：

```json
{
  "results": [
    {
      "path": "src/services/billing/retry.ts",
      "line_start": 20,
      "line_end": 78,
      "symbol": "withBillingRetry",
      "snippet": "await sleep(delayMs); delayMs *= factor;",
      "score": 0.93,
      "why_matched": "semantic similarity: retry/backoff/external API"
    }
  ]
}
```

底层建议：

- 本地 embedding 模型做初召回
- 可选本地 reranker 做重排
- 对代码按函数、类、方法切块
- metadata 中保留语言、模块、符号、路径、行号

### 6.3 `kb_search`

用途：

- 检索项目知识库和文档

适用场景：

- “这个模块为什么这样设计”
- “有没有关于租户隔离的说明”
- “部署手册里如何配置对象存储”

建议输入：

```json
{
  "query": "tenant isolation strategy",
  "kb_scope": ["docs", "adr", "runbooks", "wiki"],
  "max_results": 5
}
```

建议输出：

```json
{
  "results": [
    {
      "doc_id": "adr-012",
      "title": "ADR-012 Multi-tenant Isolation",
      "section": "Decision",
      "path": "docs/adr/012-multi-tenant-isolation.md",
      "snippet": "Each tenant gets a logical partition key...",
      "score": 0.91,
      "why_matched": "semantic similarity: tenant isolation"
    }
  ]
}
```

底层建议：

- 文档采用 `BM25 + vector` 混合召回
- 按标题层级切块
- 返回 `doc_id`、标题、章节、路径

### 6.4 `open_spans`

用途：

- 根据路径和行号范围精确回取上下文

适用场景：

- 检索到候选结果后，需要看到更完整但仍受控的上下文

建议输入：

```json
{
  "items": [
    {
      "path": "src/services/billing/retry.ts",
      "line_start": 1,
      "line_end": 120
    }
  ]
}
```

建议输出：

```json
{
  "items": [
    {
      "path": "src/services/billing/retry.ts",
      "line_start": 1,
      "line_end": 120,
      "content": "..."
    }
  ]
}
```

约束建议：

- 默认限制总字节数
- 默认限制每次回取数量
- 超限时要求客户端缩小范围

### 6.5 `index_status`

用途：

- 查询索引状态、最后更新时间、索引版本、当前仓库快照信息

适用场景：

- 模型判断检索结果是否可能过时
- 用户确认索引是否已覆盖最新代码

建议输出字段：

- `repo_root`
- `current_branch`
- `last_indexed_commit`
- `index_version`
- `last_updated_at`
- `is_dirty_worktree_supported`
- `kb_sources`

### 6.6 `reindex`

用途：

- 触发手动重建或增量更新

建议能力：

- `full`
- `incremental`
- `paths`

不建议默认让模型频繁调用，通常应限制为用户明确触发或在状态明显过期时调用。

## 7. 对 Claude Code 的额外增强

如果希望同一套 MCP 更好地服务 `Claude Code`，建议除了 tools 以外，还暴露 `resources`。

推荐资源命名：

- `repo://file/<path>`
- `repo://symbol/<symbol>`
- `kb://doc/<doc_id>`
- `kb://section/<doc_id>#<heading>`

价值：

- Claude Code 可以直接引用资源
- 适合常用文档、固定协议、设计说明
- 工具负责检索，资源负责稳定引用

第一版不一定必须实现 `resources`，但建议在架构上预留。

## 8. 检索策略

### 8.1 混合检索而不是纯向量

建议策略：

- 代码问题优先走精确搜索
- 代码的“类似实现”走语义搜索
- 文档问题走 BM25 + vector 混合召回
- 统一用 rerank 对前 N 条结果重排

推荐流程：

1. 判断查询类型
2. 路由到 exact、semantic 或 mixed
3. 召回 top 20 到 top 50
4. rerank 到 top 3 到 top 8
5. 返回短摘要
6. 需要更多上下文时再调用 `open_spans`

### 8.2 查询路由

可按规则先做一版：

- 包含报错文本、配置键、函数名、路径特征，优先 `code_exact_search`
- 包含“类似”“相似”“哪里有这种逻辑”，优先 `code_semantic_search`
- 包含“文档”“设计”“说明”“ADR”“runbook”，优先 `kb_search`

后续可以加入轻量 query classifier。

### 8.3 结果控制

每条结果建议只返回：

- `path`
- `symbol`
- `line_start`
- `line_end`
- `score`
- `why_matched`
- `snippet`

其中 `snippet` 应尽量短，建议 200 到 500 字符以内。

## 9. 索引设计

### 9.1 代码切块

优先方式：

- 使用 Tree-sitter 或语言 AST 按函数、类、方法、接口、模块级块切分

退化方式：

- 对无法稳定解析的文件，按滑动窗口切块，例如 80 到 150 行

每个 chunk 建议包含 metadata：

- `repo`
- `branch`
- `commit`
- `path`
- `language`
- `symbol`
- `chunk_id`
- `line_start`
- `line_end`
- `hash`

### 9.2 文档切块

建议按 Markdown / 文档标题层级切块：

- H1/H2/H3 为自然边界
- 每块保留标题链路
- 过长段落再做次级切分

metadata 建议包含：

- `doc_id`
- `title`
- `section`
- `path`
- `chunk_id`
- `updated_at`
- `hash`

### 9.3 忽略规则

索引时应默认忽略：

- `.git`
- `node_modules`
- `dist`
- `build`
- `coverage`
- 二进制文件
- 大型生成文件

同时尊重：

- `.gitignore`
- 用户自定义 ignore 配置

## 10. 向量模型与存储选型

### 10.1 Embedding 模型

本地部署优先考虑：

- 中文和英文都兼容的 embedding 模型
- 对代码和技术文档都有效的 embedding 模型
- 延迟稳定、批处理效率尚可

原则：

- 代码检索不要求 embedding 模型单独解决全部问题
- embedding 模型主要负责语义召回
- 精确命中依赖 exact/fuzzy 检索补齐

### 10.2 Reranker

如果机器资源允许，建议加本地 reranker。

价值：

- 减少“语义差不多但不够准”的误召回
- 对文档检索和代码相似检索提升明显

如果第一版不加 reranker，也可以先上线，再在日志中观察误召回情况。

### 10.3 向量库存储

第一版建议优先选简单、可嵌入、易维护的方案。

推荐顺序：

1. `SQLite + sqlite-vec` 或同类本地嵌入式方案
2. `LanceDB`
3. `Qdrant`
4. `Milvus`

建议：

- 单机单用户优先简单方案
- 先把检索质量跑通，再考虑分布式和高并发

### 10.4 元数据存储

元数据建议独立保存，至少可支持：

- 路径查找
- chunk 回取
- commit/version 对比
- 文档标题检索
- 索引统计信息

简单场景可直接和向量库存于同一个 SQLite 文件中。

## 11. Token 成本控制策略

这是方案是否真正“省钱”的关键。

### 11.1 必须做的事

- 检索默认只返回 top-k，小而精
- 不返回整文件
- 不返回整篇文档
- 精确上下文回取单独走 `open_spans`
- 相同查询结果可做短时缓存
- 相同 chunk 不重复展开

### 11.2 推荐限制

- `code_exact_search` 默认 `max_results <= 10`
- `code_semantic_search` 默认 `max_results <= 8`
- `kb_search` 默认 `max_results <= 5`
- `open_spans` 单次总返回字节数设上限

### 11.3 结果格式要短

坏例子：

- 直接把完整文件内容塞回模型
- 一次返回 20 个长片段
- 同一段内容在多个工具结果里重复出现

好例子：

- 先给 3 到 5 个候选
- 每个候选只有摘要和定位信息
- 模型明确需要时再请求上下文

### 11.4 成本判断

此方案节省的是远端 LLM 上下文 token，不是“系统总成本绝对更低”。

新增成本包括：

- 本地 embedding 推理
- 可选 rerank 推理
- 索引构建与增量维护

但只要仓库较大、查询频繁、模型上下文昂贵，这种成本转移通常是值得的。

## 12. 索引更新策略

### 12.1 第一版建议

采用“启动时检查 + 手动重建 + 可选文件监听”的组合。

推荐流程：

- Server 启动时检查仓库路径和索引版本
- 若 commit/hash 变化，则标记索引过期
- 用户或客户端显式调用 `reindex`
- 开发阶段可加文件监听以支持增量更新

### 12.2 增量更新

增量更新的最小粒度建议为文件级：

- 文件新增：新建 chunk
- 文件修改：删除旧 chunk，重建该文件对应 chunk
- 文件删除：清理相关 chunk

不要一开始就做复杂的 chunk 级 patch 更新，收益有限，复杂度较高。

## 13. 目录与模块建议

建议的工程结构：

```text
server/
  mcp_server.py
  tool_handlers/
    code_exact_search.py
    code_semantic_search.py
    kb_search.py
    open_spans.py
    index_status.py
    reindex.py
  retrieval/
    router.py
    exact_engine.py
    semantic_engine.py
    reranker.py
  indexing/
    code_indexer.py
    kb_indexer.py
    chunkers/
    file_watch.py
  storage/
    vector_store.py
    metadata_store.py
  config/
    settings.py
  schemas/
    tools.py
```

如果使用 TypeScript，也建议保持相同职责拆分。

## 14. 推荐实现路径

### 14.1 MVP

第一阶段只做最小闭环：

- `STDIO MCP Server`
- `code_exact_search`
- `code_semantic_search`
- `kb_search`
- `open_spans`
- `index_status`
- 手动 `reindex`
- 单仓库支持

这已经足以显著提升日常使用体验。

### 14.2 第二阶段

在 MVP 稳定后再加：

- reranker
- 增量索引
- 多仓库支持
- `resources`
- 查询日志与效果评估
- 缓存

### 14.3 第三阶段

如果未来要共享给多台机器或多人使用，再考虑：

- `HTTP / Streamable HTTP`
- 用户级权限隔离
- 远程部署
- 多租户索引

## 15. 推荐的工具描述文案

工具描述会直接影响模型是否正确调用，应写得非常具体。

### `code_exact_search`

建议描述：

> Search the current codebase for exact or near-exact matches such as symbol names, error messages, config keys, route strings, file paths, and identifiers. Use this before semantic code search when the query contains concrete text.

### `code_semantic_search`

建议描述：

> Search the current codebase for semantically similar implementations when the user asks for related logic, examples, or patterns rather than exact text matches.

### `kb_search`

建议描述：

> Search project documentation, ADRs, runbooks, and knowledge base content for design rationale, operational guidance, and business/domain context.

### `open_spans`

建议描述：

> Open specific file ranges returned by search tools to fetch precise local context. Prefer this over opening full files.

## 16. 示例调用链

### 场景一：查配置项

用户问：

> `payment_timeout_ms` 在哪里定义和使用？

理想调用：

1. `code_exact_search`
2. 返回 3 个候选片段
3. `open_spans` 打开最相关的两个片段
4. 模型总结定义位置和使用链路

### 场景二：找类似实现

用户问：

> 项目里有没有带指数退避的重试逻辑？

理想调用：

1. `code_semantic_search`
2. 返回 5 个候选函数
3. `open_spans` 打开 top 2
4. 模型比较相似点并给出建议复用点

### 场景三：问设计背景

用户问：

> 为什么这个系统用逻辑隔离而不是物理隔离？

理想调用：

1. `kb_search`
2. 返回对应 ADR 和设计文档章节
3. 如需展开，再 `open_spans` 或读取资源
4. 模型引用文档进行总结

## 17. 风险与坑点

### 17.1 纯向量检索误用

风险：

- 对精确字符串、符号名、报错文本命中差

对策：

- 强制保留 `code_exact_search`

### 17.2 工具返回过长

风险：

- token 暴涨
- 模型注意力被无关上下文稀释

对策：

- 默认短摘要
- 上下文回取单独处理

### 17.3 索引过期

风险：

- 模型基于旧代码做决策

对策：

- `index_status`
- commit/hash 校验
- 手动和增量更新机制

### 17.4 切块不合理

风险：

- 召回到了半截逻辑
- 丢失符号边界

对策：

- 优先 AST 切块
- 回取时扩大少量上下文

### 17.5 文档和代码索引混在一起

风险：

- 排序不稳定
- 回答不清楚来源是代码还是文档

对策：

- 至少逻辑上分开索引和工具入口

## 18. 效果评估指标

建议从第一版就记录日志并评估：

- 查询总数
- 工具调用分布
- 首次召回命中率
- 是否需要二次搜索
- `open_spans` 后结果是否足够
- 平均返回字节数
- 平均查询耗时
- 重复查询率

如果能人工抽样，建议评估：

- top 1 命中率
- top 3 命中率
- 误召回率
- 无结果率

## 19. 技术选型建议

如果以“尽快可用”为优先：

- 语言：`Python`
- MCP 框架：官方或社区成熟 MCP SDK
- 精确搜索：`ripgrep`
- 向量存储：`SQLite + 向量扩展` 或 `LanceDB`
- 代码切块：`tree-sitter`
- 文档切块：自定义 Markdown parser

如果你的现有生态主要在 Node.js：

- 也可以用 `TypeScript`
- 但代码 AST、文件处理、批量索引的成熟度通常 Python 更顺手

## 20. 最终建议

建议你按下面顺序推进：

1. 先做本机 `STDIO MCP Server`
2. 先把 `code_exact_search` 做扎实
3. 再接入 `code_semantic_search`
4. 文档单独做 `kb_search`
5. 用 `open_spans` 控制上下文回取
6. 先不追求复杂架构，先验证日常使用收益

最小可用版本只要满足下面三点，就已经有很高价值：

- 能快速找到准确代码片段
- 能从知识库里找到相关设计说明
- 返回结果足够短，真正减少上下文浪费

## 21. 推荐 MVP 范围

建议本项目第一版明确收敛到以下范围：

- 单机运行
- 单用户使用
- 单仓库或当前工作区使用
- 代码和文档分开索引
- 支持手动重建索引
- 支持精确检索、语义检索、知识库检索、上下文回取

不建议第一版就做：

- 多租户
- 远程 HTTP 服务
- 分布式向量库
- 复杂权限系统
- 自动全量实时索引

## 22. 一句话总结

这是一个值得做的 MCP 方向，但要按“混合检索、短结果返回、上下文二次回取”的思路来做。这样它才能同时增强 `Codex` / `Claude Code` 的代码理解能力，并在大多数真实项目里降低远端模型的 token 消耗。

## 23. 当前本地 Embedding 接口适配结论

已确认你当前这套本地向量模型接口可以直接用于本方案。

已验证特征：

- 接口形态兼容 OpenAI `embeddings` API
- `base_url` 为本地地址
- 模型名为 `text-embedding-bge-base-zh`
- 实测返回向量维度为 `768`

这意味着第一版 MCP Server 不需要自定义 embedding 协议，直接按 OpenAI 兼容方式接入即可。

建议在工程中不要直接读取个人配置文件并把密钥写入仓库，而是映射为运行时配置：

- `EMBEDDING_BASE_URL`
- `EMBEDDING_MODEL`
- `EMBEDDING_API_KEY`

建议值示意：

```text
EMBEDDING_BASE_URL=http://127.0.0.1:1234/V1
EMBEDDING_MODEL=text-embedding-bge-base-zh
EMBEDDING_API_KEY=本地环境变量注入
```

说明：

- `base_url` 末尾保留 `/V1` 即可
- 实际请求路径为 `/embeddings`
- 如果不同本地服务对大小写敏感，工程里不要强行改写 URL

## 24. 在本方案中的接入方式

### 24.1 推荐职责划分

这套本地 embedding 模型建议只承担“语义召回”职责，不承担全部检索职责。

推荐分工：

- `code_exact_search` 负责精确命中
- `code_semantic_search` 通过该模型生成向量做语义召回
- `kb_search` 使用该模型做文档向量检索
- reranker 如需接入，可后续独立增加

原因：

- `bge-base-zh` 这类模型很适合中文语义检索
- 但代码符号、配置键、路径、报错字符串仍然应优先走精确检索
- 对代码场景，embedding 是补充，不应单独作为唯一搜索手段

### 24.2 查询流建议

代码库查询：

1. 先判断是否是精确型查询
2. 若是，优先 `code_exact_search`
3. 若不是，再调用 embedding 做 `code_semantic_search`
4. 返回 top-k 结果
5. 再通过 `open_spans` 拉精确上下文

知识库查询：

1. 文本切块
2. 生成向量
3. 向量召回
4. 可选叠加 BM25
5. 返回文档摘要

## 25. 建议的配置文件设计

建议你的 MCP Server 自己维护一份独立配置，不直接耦合个人模型配置文件路径。

例如：

```json
{
  "embedding": {
    "provider": "openai_compatible",
    "base_url": "http://127.0.0.1:1234/V1",
    "model": "text-embedding-bge-base-zh",
    "api_key_env": "EMBEDDING_API_KEY",
    "dimensions": 768
  },
  "index": {
    "repo_root": "./workspace",
    "vector_store": "lancedb",
    "chunk_size_lines": 120
  }
}
```

这样做的好处：

- 模型来源可替换
- 不把个人绝对路径写死到项目
- 后续可以轻松增加第二个 embedding 模型

## 26. Python 接入示例

如果第一版用 `Python`，可以直接按 OpenAI 兼容接口封装 embedding client。

示例：

```python
import os
from openai import OpenAI


class EmbeddingClient:
    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        self.model = os.environ["EMBEDDING_MODEL"]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in resp.data]
```

建议：

- 对索引场景采用批量 embedding
- 对在线查询单条或小批量调用
- 在服务启动时做一次健康检查和维度校验

## 27. 服务启动时的健康检查

建议 MCP Server 启动时检查以下内容：

- embedding 接口是否可连通
- 返回向量维度是否为预期值 `768`
- 向量库当前 collection 的维度是否一致
- 若维度不一致，则拒绝加载旧索引并提示重建

原因：

- 向量维度一旦变化，旧索引通常不可复用
- 这类错误如果不提前拦截，会在检索阶段才暴露

## 28. 针对中文项目的建议

你的模型名是 `text-embedding-bge-base-zh`，这对中文知识库是加分项。

建议策略：

- 中文文档直接用该模型
- 中文注释较多的代码仓库也可以直接使用
- 如果未来英文代码和英文文档占比很高，可考虑双模型或换成中英兼容更强的 embedding 模型

第一版先不必复杂化。只要你的主要查询是中文描述项目逻辑、中文问答、中文知识库检索，这个模型是可以先跑起来的。

## 29. 基于你当前接口的最终落地建议

基于你现在这套接口，建议第一版按下面方式收敛：

1. MCP Server 用 `Python`
2. embedding 按 OpenAI 兼容接口接入
3. 向量维度固定按 `768` 建库
4. 代码检索保留 `exact + semantic` 双通道
5. 文档检索走 `BM25 + embedding`
6. 所有工具默认短返回，全文通过 `open_spans` 获取

这条路线实施难度最低，而且和你现有本地模型配置兼容性最好。
