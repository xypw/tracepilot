# 审批、执行回执与运行时 Skill 修复验证（2026-10-05）

## 修复与取舍

- 正式执行回执由工作区、任务编号及补丁摘要共同标识。同一任务重复确认复用回执；新任务不能误用旧任务的成功结果。旧版未完成回执仍阻断执行，避免升级后自动重试结果未知的写入。
- 同一工作流实例按任务串行处理启动和审批；拒绝重复使用任务编号，已接受的审批决定不能被反向覆盖。恢复已持久化的批准时，依据执行回执返回结果或停止在 `RESULT_UNKNOWN`。
- 正式测试无论成功还是失败，均重新检查目标文件；检测到外部编辑时保留内容，不标记成功、不自动回滚覆盖外部编辑。补丁后字节数在预览与写入前检查，避免越过文件大小限制后无法回滚；覆盖中文 UTF-8 和 CRLF。
- 模型调查器每轮请求加载安装包固定位置的 `java-test-triage` Skill。目标仓库的同名文件不能替换规程；缺失、空白、非法编码/元数据或链接配置明确失败。Skill 负责调查指引，权限仍由文件策略、审批和执行器控制。
- 开发回归 Skill 补充执行身份、并发审批、源码漂移与运行时 Skill 加载的测试映射。

## 已执行验证

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 112 项：101 通过、11 跳过、0 失败 |
| 跳过范围 | 10 项因 Windows 无符号链接创建权限跳过；1 项真实 Docker 沙箱测试未启用 |
| 真实 Java 运行 | 全量测试包含固定模型动作回放及本机 javac/java 执行，不调用外部模型 |
| 固定故障端到端评测 | 12 类构造故障，确定性规则规划器；下列 8 个字段逐例均为 12/12 |

逐例检查：`baseline_failed`、`localized_expected_file`、`proposed_expected_file`、`unchanged_before_confirmation`、`checkpoint_recovered`、`task_succeeded`、`targeted_test_passed`、`duplicate_confirmation_idempotent`。见[原始 JSON](../reports/review-fixes-20261005.json)。

pytest 的 1 条 warning 是 Starlette 对 AnyIO 旧别名的弃用提醒，不是测试失败。本轮未重建/运行 Docker，未调用真实模型；12/12 不代表真实模型修复成功率。

## 复现

本机需已有项目虚拟环境与 JDK。为避免 Windows 默认临时目录的权限冲突，可在仓库根目录运行：

```powershell
$traceTestTemp = Join-Path (Get-Location) ('.tracepilot\review-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $traceTestTemp -Force | Out-Null
$env:TEMP=$traceTestTemp
$env:TMP=$traceTestTemp
$env:TRACEPILOT_DOCKER_TESTS='0'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -ra -o addopts='' -p no:cacheprovider --basetemp (Join-Path $traceTestTemp 'pytest')
.\.venv\Scripts\python.exe -X utf8 -m scripts.run_evaluation --output reports/review-reproduction.tmp.json
```

评测脚本显式使用本机执行器，只运行仓库内受信任的固定 Java 样例；不要用此模式接收陌生仓库。输出使用独立文件，不覆盖本次历史记录。

## 未覆盖边界

审批锁仅覆盖单个工作流实例；不提供跨进程/多实例审批协调。文件写入与 SQLite 回执不是同一原子事务，异常后保留结果未知并要求人工核对。测试结束时的漂移检查也不是对外部进程的文件锁，不能保证检查后文件永不变化。恢复测试使用持久化状态和对象重建，不等同于任意时刻进程崩溃注入。
