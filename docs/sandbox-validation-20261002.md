# Java 测试容器验收（2026-10-02）

## 结果与条件

- 33 项定向回归通过，覆盖沙箱参数、输出预算、超时清理、运行器错误分类，以及 API、审批、恢复和试修相关流程。
- 1 条真实 Docker 集成测试通过：编译并运行仓库测试生成的虚构 `demo.SandboxProbe`。
- 环境：Windows Docker Desktop，Docker Engine 29.7.2，Linux 容器及 cgroup v2。
- 镜像：`eclipse-temurin:17-jdk-jammy`，本轮拉取记录的 digest 为 `sha256:43f4431cc895d37ceb115e2e1f160545ec45caee9c1cb4246fc6bb879d58aeda`。
- 未调用外部模型。本记录验证执行边界，不统计模型诊断或修复成功率。

## 真实容器中的断言

| 检查 | 结果 |
| --- | --- |
| UID 为 65534，Linux effective capabilities 为 0，NoNewPrivs 为 1 | 通过 |
| 容器只有 loopback 网卡 | 通过 |
| 修改只读源码、在只读根目录创建文件 | 均被拒绝 |
| 在临时目录编译并运行 Java 测试 | 通过 |
| 测试用宿主机环境变量、`.env` 文件及 Docker Socket | 均不可见 |
| cgroup 内存上限 512 MiB、进程数上限 64 | 通过 |
| 正式工作区源码在执行前后保持一致 | 通过 |

输出超限、超时和 Docker 125/126/127 错误采用定向回归验证；没有把这些模拟断言描述为真实容器故障注入。输出读取仅保留 4 KB 尾部，默认总预算为 1,000,000 字节；超过预算停止对应容器，Docker 日志驱动设为 `none`。

## 复现

先由操作者准备可信的上述 JDK 镜像并启动 Docker。测试执行器使用 `--pull=never`，不会自行下载镜像。运行命令：

```powershell
python -m pytest tests/test_sandbox.py tests/test_api.py tests/test_workflow.py tests/test_model_evaluation.py tests/test_agentic_repair.py tests/test_runner_environment.py -q
$env:TRACEPILOT_DOCKER_TESTS = "1"
python -m pytest tests/test_sandbox_integration.py -q
Remove-Item Env:TRACEPILOT_DOCKER_TESTS
```

## 使用边界

默认离线工作流与真实模型工作流使用独立测试容器；真实模型的基线、试修和确认后复测使用同一运行器策略。Docker 不可用时任务停止。内置 Docker Compose 演示显式使用可信虚构样例的本地运行器，未挂载 Docker Socket，不作为陌生仓库执行入口。

这些限制降低 Java 测试接触宿主机数据与网络的风险，不覆盖容器运行时漏洞、恶意基础镜像或一切资源耗尽攻击。仅支持当前单模块 Java 主类测试；测试通过不证明补丁语义完全正确。
