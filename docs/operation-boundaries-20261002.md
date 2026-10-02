# 文件、编译和容器边界集成回归（2026-10-02）

## 改动范围

读取、搜索与源码收集统一复用 `WorkspacePolicy`：拒绝隐藏路径、`target`、`node_modules`、反斜杠、控制字符、目录穿越、链接及 Windows 重解析点。搜索在进入目录前剪枝；编译仅收集 `src/main/java/` 和 `src/test/java/`，遇到源码链接、超大源码或总数量超限立即拒绝。

Javac 本地运行器与 Docker 运行器共用源码列表。两者显式固定类路径、关闭隐式源码路径，并禁用隐式源码编译及注解处理。补丁预览提前验证生产源码范围，测试源码不进入审批预览。

本记录对应统一策略后的实现与验证；既有 Docker 沙箱、审批、恢复、回滚与回执逻辑保留。

## 本机结果

| 检查 | 结果及口径 |
| --- | --- |
| 9 个相关测试文件 | 59 passed，0 failed，10 skipped |
| Windows 真实目录联接 | 上述通过项中包含 2 项，源码根及子目录联接均在 Java/Docker 执行前被拒绝 |
| 跳过原因 | 10 项真实符号链接创建返回 WinError 1314；不计入通过，也不代表符号链接的 Windows 实测完成 |
| 真实 Docker Java 探针 | 1 passed，0 failed，0 skipped；统一源码收集和新编译参数下再次通过 |
| 固定离线规则规划器案例 | 12 类，以下全部字段逐例检查均为 12/12 |
| Skill 校验 | 开发验证 skill 及原调查指导 skill 的元数据校验通过 |

检查的离线字段：原故障先失败、定位预期文件、提案预期文件、确认前源码不变、检查点恢复、批准后任务完成、Java 定向测试通过、重复确认幂等。原始结果在 [operation-boundaries-20261002.json](../reports/operation-boundaries-20261002.json)。

未调用外部模型；规则规划器结果不表示真实模型修复成功率。检查点测试是重建工作流对象后恢复，未扩展成操作系统进程崩溃测试。

## 复现命令

使用安装了项目依赖的 Python 环境及 JDK 17+：

```text
python -m pytest tests/test_security.py tests/test_tools.py tests/test_runner_environment.py tests/test_sandbox.py tests/test_windows_reparse.py tests/test_api.py tests/test_workflow.py tests/test_agentic_repair.py tests/test_model_evaluation.py -q -ra
python -m scripts.run_evaluation --output reports/boundary-integration.tmp.json
```

真实 Docker 验收的镜像和命令见 [容器验收记录](sandbox-validation-20261002.md)。Linux 上 Windows 专用目录联接测试会跳过；符号链接测试需具备本地创建链接的权限。不要将不同环境的通过与跳过数量直接相加成一轮测试结果。

## 限制

操作者指定的工作区根被视为信任边界；工作区内部路径仍逐级验证。目录校验不是抵御另一宿主机进程持续恶意替换文件的完整锁定机制。容器运行时漏洞、恶意基础镜像及补丁语义正确性不由这些回归证明。
