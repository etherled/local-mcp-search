# Automated Benchmark

这个目录提供一套最小自动 benchmark harness，用来比较：

- `Codex`
- `Claude`
- `baseline`
- `local-search`

默认任务数是 `4`，所以完整一轮是 `16 runs`。

## 目标

这套脚本优先解决两个问题：

- 尽量自动执行，不靠手工切换客户端
- 自动落盘结果，便于后续汇总和回写 README

当前版本只覆盖“可自动判分”的只读任务，不做自动代码修改 benchmark。

## 文件

- `tasks.json`
  - 基准任务定义
- `README.md`
  - 运行说明

执行脚本在：

- [scripts/run_benchmark.py](/D:/trae_prj/mcp_sd/scripts/run_benchmark.py:1)

## 运行

完整运行：

```powershell
python .\scripts\run_benchmark.py
```

默认行为：

- 跑 `4 tasks x 2 clients x 2 modes = 16 runs`
- case 间默认暂停 `12` 秒
- 识别明显的 `429 / rate limit` 错误后自动退避重试，默认最多 `2` 次，基础退避 `30` 秒
- `Codex` 默认使用 `schema` 输出模式，也就是通过 `--output-schema` 请求结构化输出

只跑一个客户端：

```powershell
python .\scripts\run_benchmark.py --clients codex
python .\scripts\run_benchmark.py --clients claude
```

如果你的 `Codex` 不是官方直连，而是经由第三方 OpenAI 兼容转发，建议先改成 plain JSON 模式：

```powershell
python .\scripts\run_benchmark.py --clients codex --codex-output-mode plain
```

两种 `Codex` 输出模式含义：

- `--codex-output-mode schema`
  - 走 `codex exec --output-schema ...`
  - 更严格，结果更稳定
  - 但部分转发供应商会在这条链路上报 `429` 或不兼容 `response_format`
- `--codex-output-mode plain`
  - 不走 `--output-schema`
  - 只要求模型最后输出 JSON 文本，脚本再本地解析
  - 只适合本身确实兼容当前 `Codex exec` 链路、但不兼容 structured output 的供应商

只跑一部分模式：

```powershell
python .\scripts\run_benchmark.py --modes baseline
python .\scripts\run_benchmark.py --modes local-search
```

先做单任务 smoke test：

```powershell
python .\scripts\run_benchmark.py --task-ids repo-overview-entrypoints --clients codex --modes baseline
```

调整限流相关参数：

```powershell
python .\scripts\run_benchmark.py --pause-seconds 0 --max-retries 0
python .\scripts\run_benchmark.py --pause-seconds 20 --retry-backoff-seconds 45
```

## 输出

结果会写到：

- `benchmark/results/<run_id>/summary.json`
- `benchmark/results/<run_id>/<case_id>/result.json`

每个 case 会记录：

- 客户端
- 模式
- 耗时
- session id
- token usage
- 结构化输出
- 自动判分结果
- 原始输出文件路径

## 当前结论

当前仓库已经拿到两批可复现样本：

- `Claude`：`benchmark/results/20260509-204132-f33bdb48`
- `Codex`：`benchmark/results/20260509-170327-d1209b40`

`Claude` 样本：

- `baseline 4/4`，`local-search 4/4`
- 总耗时：`66.653s -> 56.259s`
- 总计费：`0.651079 -> 0.443238`
- 总轮次：`27 -> 18`
- 总 token：`366078 -> 379791`
- 当前结论：`local-search` 对 `Claude` 的主价值是保持成功率不降的前提下，降低计费、提速、减少轮次；`token` 更适合作为诊断信息

`Codex` 样本：

- `baseline 4/4`，`local-search 4/4`
- 总耗时：`215.693s -> 207.573s`
- 总 token：`730540 -> 570248`
- 当前结论：`local-search` 对 `Codex` 既有轻微提速，也有明确的 token 节省价值

因此，这个 benchmark 现在更适合得出这样的判断：

- 不要强行给所有客户端套同一套主指标
- `Claude` 更适合看 `成功率 + 计费 + 耗时 + 轮次`
- `Codex` 更适合看 `成功率 + 耗时 + token`
- `local-search` 的收益和所接入的 agent / 模型链路有关，不是所有客户端都同一种收益结构

当前收口说明：

- 本轮 benchmark 先到此为止，不再继续为不兼容当前 `Codex CLI` 的第三方链路补测
- `Mimo` 与本次直连 `DeepSeek` 配置都不适合当前 `Codex CLI` 的 `Responses API` 路径，因此不纳入 `Codex` 正式结论
- 后续如果再补 `Codex` 数据，应只选择已确认兼容当前 `Codex exec` 的链路

## 注意

- `baseline` 不挂 `local-search`
- `local-search` 会临时准备本地 wrapper 和 reindex，但不应该改你的日常 `cpx` 使用配置
- 当前脚本默认只适合真实已登录的本机环境
- 当前脚本没有接 Langfuse，先把自动执行和自动记录跑通
- 如果 `Codex` 或 `Claude` 账户本身已经触发强限流，脚本会记录失败原因，但不能替代账号侧配额恢复
- 如果 `Codex` 交互式对话正常，但 benchmark 中 `schema` 或 `plain` 模式仍失败，通常说明该供应商对 `exec` / `Responses API` 链路本身不兼容，而不是 benchmark 并发问题
