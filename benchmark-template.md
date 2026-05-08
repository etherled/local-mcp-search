# Benchmark Template

这个模板用于验证 `local-search` 对 `Codex` / `Claude Code` 的实际增效降本效果。

建议每个任务至少跑两组：

- `baseline`：不使用 `local-search`
- `local-search`：按推荐工作流使用 `repo_overview` / `code_exact_search` / `file_outline` / `open_spans` / `code_context_pack` 等工具

如果要继续细分，可再加：

- `local-search-no-reranker`
- `local-search-with-reranker`

## 1. 任务信息

```text
任务名称:
任务类型:
仓库:
日期:
执行人:
客户端: Codex / Claude Code
模型:
```

任务类型建议从以下选择：

- 找函数定义
- 找字符串引用
- 理解模块
- 小范围代码修改
- 恢复最近会话
- code review
- change summary

## 2. 实验设置

```text
模式: baseline / local-search / local-search-no-reranker / local-search-with-reranker
是否开启 reranker:
是否已有索引:
是否命中最近会话恢复:
工作区规模:
备注:
```

## 3. 操作记录

```text
开始时间:
结束时间:
总耗时:
工具调用数:
主要工具:
读取文件次数:
读取片段次数:
失败重试次数:
```

主要工具示例：

- `repo_overview`
- `dependency_overview`
- `code_exact_search`
- `symbol_search`
- `file_outline`
- `open_spans`
- `code_context_pack`
- `change_context`

## 4. 成本记录

```text
最终上下文字符数:
最终 token 估算:
中间无效读取字符数:
最终提交给模型的关键片段数:
是否出现整文件盲读:
```

建议至少统一记录：

- 最终上下文字符数
- 最终 token 估算
- 是否有重复搜索
- 是否有明显无效读取

## 5. 结果质量

```text
是否一次命中:
是否得到正确定位:
是否完成任务:
是否需要人工纠偏:
最终输出质量: 高 / 中 / 低
```

判断标准建议：

- `一次命中`：第一次主要搜索后就找到了正确入口
- `正确定位`：找到的函数 / 文件 / 文档确实是目标位置
- `完成任务`：最终回答或修改已满足需求
- `人工纠偏`：需要人手动指出“找错地方了”或“继续看别处”

## 6. 结果摘要

```text
结论:
local-search 是否更快:
local-search 是否更省上下文:
local-search 是否更稳:
最有价值的工具:
最没价值的步骤:
```

## 7. 对比表

| 指标 | baseline | local-search | 备注 |
| --- | --- | --- | --- |
| 总耗时 |  |  |  |
| 工具调用数 |  |  |  |
| 最终上下文字符数 |  |  |  |
| 最终 token 估算 |  |  |  |
| 一次命中 |  |  |  |
| 是否完成任务 |  |  |  |

## 8. 推荐首批任务集

建议先做 5 个任务，不要造题，直接选真实工作流：

1. 在中型仓库里找到一个已知函数定义
2. 找一个配置键或报错文本的全部引用
3. 理解某个模块并只改一处逻辑
4. 从最近会话恢复后继续改动
5. 对一组未提交代码做 review / change summary

## 9. 结果发布建议

在没有至少 10 到 20 组可复现记录前，不建议对外宣传具体节省比例。

在数据足够前，更稳妥的表述是：

- 更适合中大型仓库
- 更适合 resume / review / precise lookup
- 有明确的上下文压缩潜力
- 已观察到工具调用链更短，但仍在补正式 benchmark
