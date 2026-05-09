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

只跑一个客户端：

```powershell
python .\scripts\run_benchmark.py --clients codex
python .\scripts\run_benchmark.py --clients claude
```

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
- cost
- 结构化输出
- 自动判分结果
- 原始输出文件路径

## 注意

- `baseline` 不挂 `local-search`
- `local-search` 会临时准备本地 wrapper 和 reindex，但不应该改你的日常 `cpx` 使用配置
- 当前脚本默认只适合真实已登录的本机环境
- 当前脚本没有接 Langfuse，先把自动执行和自动记录跑通
- 如果 `Codex` 或 `Claude` 账户本身已经触发强限流，脚本会记录失败原因，但不能替代账号侧配额恢复
