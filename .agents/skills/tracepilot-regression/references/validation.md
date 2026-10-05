# 验证指南

以下路径均相对 TracePilot 仓库根目录。使用项目虚拟环境；Windows 通常为 `.venv/Scripts/python.exe`，Linux 为 `.venv/bin/python`。

## 按改动选择

| 改动 | 相关检查 |
| --- | --- |
| `security.py` | `tests/test_security.py`；直接读取与搜索限制一致，链接/隐藏路径、源码数量和大小拒绝 |
| `tools.py` | `tests/test_tools.py tests/test_runner_environment.py`；预览、按执行身份隔离回执、成功/失败后的源码漂移、回滚及编译参数 |
| `sandbox.py`、`run_java_test.sh` | `tests/test_sandbox.py`；需要真实隔离验收时再运行 opt-in Docker 测试 |
| `factory.py`、`workflow.py`、API | `tests/test_workflow.py tests/test_api.py`；并发批准/拒绝、跨任务回执、恢复、幂等及默认运行器 |
| 模型调查流程 | `tests/test_agentic_repair.py tests/test_model_evaluation.py`；动作回放不等于外部模型效果 |
| 运行时 `java-test-triage` Skill 或其加载入口 | `tests/test_skill_loading.py`；确认实际模型请求包含安装包中的指引、调查仓库不能替换它、缺失或无效配置明确失败 |

若改动跨越上述层，可组合相关测试，避免反复运行同一组。pytest 临时目录权限冲突时，使用仓库内新的专用 `--basetemp`，不要清理用户通用临时目录。若环境不支持创建符号链接，记录对应跳过项，不算通过。

## 离线案例

需要验证整个固定审批链路时运行：

```text
python -m scripts.run_evaluation --output reports/boundary-regression.tmp.json
```

该入口仅用于本仓库可信虚构夹具，显式使用本地 JDK 运行器。检查报告 `metadata.mode` 为 `offline_rule_based_planner`，对每个 case 核对：

- `baseline_failed`
- `localized_expected_file`、`proposed_expected_file`
- `unchanged_before_confirmation`
- `checkpoint_recovered`
- `task_succeeded`、`targeted_test_passed`
- `duplicate_confirmation_idempotent`

任一字段失败都应单独报告。CLI 的成功退出主要依据任务终态，不能代替其他字段。保留逐例原始结果；`*.tmp.json` 是本地临时报告，正式交付证据需使用明确的非临时路径并先检查敏感内容。

## 真实容器

Docker 引擎可用且可信 JDK 镜像已准备时，设置 `TRACEPILOT_DOCKER_TESTS=1` 后运行 `tests/test_sandbox_integration.py`。该探针只编译虚构 Java 源码，不调用模型。检查容器用户、网络、只读挂载、凭据隔离和资源上限；支持范围见仓库 `docs/sandbox-validation-20261002.md`。

普通 pytest 会跳过该 opt-in 测试。它未运行时应明确写“真实容器未验收”，不要只展示其余测试全绿。不要给 API 容器挂载 Docker Socket 来绕过运行器不可用。

## 证据边界

外部模型请求需要当前任务已有的数据与目的地授权；没有对应实际结果就不更新真实模型指标。文件策略与 Docker 隔离分别验证，前者不能证明不可信 Java 测试无法访问操作系统。
