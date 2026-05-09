# GitHub 行动计划

## 1. 目标定位

当前项目适合以如下定位公开发布：

- `alpha / 0.1.x`
- `Windows-first`
- 面向 `Codex` / `Claude Code` 的本地检索型 MCP
- 强调 `agent-oriented local search`
- 强调“增效降本方向明确”，但暂不宣传具体量化节省比例

不建议当前阶段的对外表述：

- 不宣称“跨平台成熟可用”
- 不宣称“零配置开箱即用”
- 不宣称“显著节省 XX% token”，除非有正式 benchmark
- 不宣称“企业级产品化完成”

建议对外核心一句话：

> 一个面向 Codex / Claude Code 的本地 MCP 检索层：本地负责找、排、压缩上下文，远端大模型只负责理解、决策和修改。

## 2. 发布前行动计划

### 当前进度快照（2026-05-09）

已完成：

- `launcher` 默认值已改成安全默认值，不再把个人绝对路径写进仓库默认配置
- `LICENSE` 已补齐，`pyproject.toml` 描述已更新为本地 `llama-server` 部署表述
- `README.md` 已补齐平台边界、smoke test、`cpx` 默认行为、`doctor`、resources 与使用建议
- `cpx` 空参默认启动 `Codex`，并按 workspace 恢复最近会话；`cpx -Claude` 恢复最近 Claude 会话
- `doctor` 已落地；`codex_mcp_matches_workspace` 可用于排查 MCP 指向旧工作区的问题
- `repo://overview`、`repo://dependency-summary`、`repo://changes` 已落地
- `change_context` 已做 Windows 稳定性收口，当前优先保证“不长时间卡死”
- 自动 benchmark harness 已落地，支持 `4 tasks x 2 clients x 2 modes = 16 runs`，并自动记录 `summary / result / raw output`

进行中：

- 清理公开仓库中不该提交的本地说明文件、私有运行产物
- 继续评估 `change_context` 是否需要在现有稳定方案上再增强

待后续执行：

- 扩大 `Claude + Xiaomi Mimo 2.5 Pro` 的 benchmark 样本规模
- 视网络条件决定是否补 `Codex` 的正式对照数据
- 将 benchmark 结果继续回写 README / GitHub 发布文案
- demo / issue 模板等发布后补充物料

### P0：必须完成

1. 清理个人环境硬编码

- 处理 [launcher.py](/D:/trae_prj/mcp_sd/src/local_mcp_search/launcher.py:21) 中的绝对路径默认值
- 改成“环境变量优先 + README 示例值”
- 至少包括：
  - `DEFAULT_LLAMA_SERVER`
  - `DEFAULT_EMBED_GGUF`
  - `DEFAULT_RERANK_GGUF`

完成标准：

- 新用户不需要修改源码就能看懂如何配置
- 项目默认值即使不可运行，也不能暴露个人目录信息

2. 补充 `LICENSE`

- 推荐 `MIT`
- 如果你希望保留更强控制，可选 `Apache-2.0`

完成标准：

- 仓库根目录存在标准许可证文件

3. 调整项目元信息

- 更新 [pyproject.toml](/D:/trae_prj/mcp_sd/pyproject.toml:1) 的 `description`
- 从“OpenAI-compatible embeddings”转成更符合当前事实的描述

建议文案方向：

- Local MCP server for code and knowledge retrieval with local llama-server based embedding and reranking.

4. 明确平台边界

- 在 README 顶部加清晰说明：
  - 当前优先支持 `Windows 10/11 x64`
  - 主要围绕 PowerShell / Codex / Claude Code 的 Windows 工作流验证

完成标准：

- 新用户打开 README 就能知道当前支持边界

5. 补一套最小 smoke test 文档

建议写成 README 独立小节：

1. `python -m local_mcp_search.cli reindex --mode auto`
2. `python -m local_mcp_search.cli status`
3. `cpx`
4. `cpx -Claude`
5. `codex mcp get local-search`
6. `claude mcp get local-search`

完成标准：

- 外部用户能按步骤判断“安装是否成功”

6. 清理不该进公开仓库的本地文件

- `.claude/`
- `.mcp.json`
- `codex resume *.txt`
- 本地临时说明文档
- 其他会暴露个人环境或会话内容的文件

完成标准：

- 仓库根目录不存在明显私人运行产物

当前状态：

- `1` 到 `5` 已完成
- `6` 仍需在正式公开前最后清理一轮

### P1：强烈建议完成

1. 新增“为什么不直接用 grep / Read / Bash”的说明

建议在 README 中补一个对比例子：

- 传统流程：
  - `rg`
  - 打开整文件
  - 再 `rg`
  - 再读整文件
- 当前 MCP 流程：
  - `code_exact_search`
  - `file_outline`
  - `open_spans`
  - 或直接 `code_context_pack`

目标：

- 让用户快速理解项目不只是“换个搜索壳”

2. 增加“适合什么场景 / 不适合什么场景”

适合：

- 中大型仓库
- 需要频繁恢复工作上下文
- 需要同时查代码和知识库
- 依赖 Codex / Claude Code 的日常开发流

不适合：

- 很小的仓库
- 几乎不需要语义搜索
- 不愿维护本地模型
- 追求跨平台零配置

3. 增加“已知限制”

建议明确写：

- Windows-first
- 当前对本地 `llama-server` 部署有依赖
- 首次索引耗时受模型和磁盘影响明显
- 尚未提供正式 benchmark
- 尚未围绕 Linux / macOS 做完整验证

4. 加一张架构图

建议画最小结构图：

- `Codex / Claude Code`
- `MCP client`
- `local-search`
- `ripgrep`
- `LanceDB`
- `llama-server embedding`
- `llama-server reranker`

目标：

- 降低理解成本

### P2：发布后逐步推进

1. 补 benchmark

建议至少做三类简单对比：

- `grep + 手工读文件` vs `code_context_pack`
- 无 reranker vs 有 reranker
- 不使用 local-search vs 使用 local-search 的平均输入 token

建议指标：

- 首次定位耗时
- 返回上下文字符数
- 最终送给远端模型的 token 规模
- 首次命中率

注意：

- 没数据前不要宣传比例
- 当前状态：自动 benchmark harness、任务集和结果落盘结构已补齐；`Claude + Xiaomi Mimo 2.5 Pro` 首轮 `4 tasks` 样本已跑通，`local-search` 在 `4/4` 成功率下，将总耗时从 `93.289s` 降到 `67.962s`，总费用从 `$0.5864` 降到 `$0.4280`

执行方式建议：

- 先挑 5 到 10 个真实任务，不要造题
- 每个任务跑两组：`baseline` 和 `local-search`
- 记录工具调用数、总耗时、最终上下文大小、是否一次命中
- 只做“能复现、能解释、能贴 README”的粗基准，不追求复杂统计

建议先覆盖的任务类型：

- 找函数定义
- 找字符串引用
- 理解一个模块并做一处小改动
- 恢复最近会话继续改
- code review / change summary

记录模板：

```text
任务:
仓库:
模式: baseline / local-search
工具调用数:
总耗时:
最终上下文字符数:
最终 token 估算:
是否一次命中:
备注:
```

建议直接落成独立文件，便于后续反复复用：

- [benchmark-template.md](/D:/trae_prj/mcp_sd/benchmark-template.md:1)

2. 准备 demo 仓库或 demo 录屏

建议做两个短 demo：

- “从需求到定位到修改”
- “恢复最近会话 + 接着改”

3. 整理 issue 模板

- bug report
- feature request
- model/backend compatibility

4. 如果 star 和 issue 证明有需求，再考虑跨平台

- `Linux`
- `macOS`

当前不建议提前投入

## 3. 功能补充建议

前提：

- 当前主方向应保持“先稳住”
- 不建议为了“看起来更强”继续膨胀底层搜索工具数量
- 优先做真正影响 agent 工作流效率的功能

### 3.1 已落地：补诊断而不是补花活

当前已提供统一诊断入口：

- `doctor`

当前已覆盖或已检查：

- llama-server 是否存在
- embedding / reranker 端口是否可用
- GGUF 路径是否存在
- 索引目录是否存在
- 向量维度是否匹配
- 当前 workspace 是否可写
- `codex` / `claude` 是否在 PATH

价值：

- 这比再加一个搜索工具更能减少外部用户的卡死和流失

### 3.2 次高优先级：补索引失配防护

建议在启动或 `status` 时显式检查：

- embedding 维度与旧索引维度是否一致
- 模型切换后是否需要强制 `reindex full`

价值：

- 这是公开发布后最容易踩到的稳定性问题之一

### 3.3 高价值：补“查询调试信息”开关

建议增加可选 debug 输出：

- 精确搜索命中了哪些文件
- 语义召回的候选数量
- reranker 实际参与重排的数量
- `code_context_pack` 最终裁剪掉多少字符

价值：

- 方便你自己调优
- 也方便外部用户提 issue

### 3.4 高价值：补 ignore / include 配置能力

建议支持项目级配置：

- 额外忽略目录
- 文档目录白名单
- 大文件排除
- 特定语言优先级

价值：

- 一旦公开给他人使用，仓库形态差异会明显变大
- 这类配置比新增工具更实用

### 3.5 已落地：补资源型输出

当前已提供 MCP resources：

- `repo://overview`
- `repo://dependency-summary`
- `repo://changes`

价值：

- Claude 对 resources 的利用通常更自然
- 适合稳定、可重复读取的信息

### 3.6 已部分落地：继续补强 change_context

当前已经完成：

- 按文件类型分组
- 标注新增 / 修改 / 删除
- 风险提示
- git `numstat` 摘要

后续如果继续做，建议只补真正影响稳定性或可读性的部分：

- 进一步压缩 MCP 通道里的超时概率
- 在稳定前提下补更精炼的 diff 摘要
- 保持 git-only / light-weight 回退路径，避免 resume / review 时再次卡死

价值：

- 对 review、resume、接手他人改动很有帮助

### 3.7 暂缓：不要急着继续增加搜索工具

暂不建议优先新增：

- 更多“类似 search 的 search”
- 更复杂的 agent orchestration
- 多工作区统一大索引
- 远程服务化部署

原因：

- 这些东西会明显拉高复杂度
- 当前真正缺的是发布可用性、诊断能力和量化证据

## 4. 发布文案建议

建议在 GitHub 首页强调：

- local-first
- agent-oriented
- Windows-first
- Codex / Claude Code
- context compression
- local embedding + local reranking

建议标题风格：

> local-mcp-search: an agent-oriented local search MCP for Codex and Claude Code

建议一句话描述：

> Local codebase search, reranking, and context compression for Codex and Claude Code, with workspace-aware session recovery and local llama-server deployment.

## 5. 剩余执行顺序（截至 2026-05-09）

1. 扩大 `Claude + Xiaomi Mimo 2.5 Pro` 的 benchmark 样本，补更多真实任务
2. 将扩样后的 benchmark 结果继续回写 README 与 GitHub 首页文案
3. 视网络条件决定是否补 `Codex` 的正式对照数据
4. 准备 demo / issue 模板等发布辅助材料
5. 发 GitHub

## 6. 当前结论

当前项目已经具备公开发布价值，而且核心链路已经能跑通，但更适合以“早期高质量工具”的姿态发布，而不是以“成熟通用平台”的姿态发布。

如果按这个计划推进，项目会更像：

- 一个可信的、方向清晰的 agent 工具项目

而不是：

- 一个只在作者电脑上勉强可跑的个人脚本集合
