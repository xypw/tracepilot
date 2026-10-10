# 本机部署验收：2026-10-09

## 版本和部署

- 应用源码：`f6cec174722d0c3c4095b1afdcb1fd5bcaddcd3b`。
- 镜像：`sha256:441371df5aab03feb755fd6f9d24a8adea4e9ed19db8ac2b0cc403b13194384b`。
- 演示入口：`http://127.0.0.1:8020/demo`，健康检查与页面均返回 200。
- 仅绑定本机 `127.0.0.1:8020`；未挂载宿主机目录或 Docker Socket。
- 演示使用离线规则规划器与内置可信 Java 夹具，不调用外部模型。镜像中的 12 个 Python 文件与本地对应源码哈希一致，随应用打包的 Skill 可加载。

## 验收结果

脚本与逐项原始结果：

- [独立验收脚本](../scripts/deployment_acceptance_20261009.py)
- [26 项断言报告](../reports/deployment-20261009.json)

脚本在运行环境的临时目录中复制内置夹具，通过 FastAPI TestClient、真实 javac/java 和 SQLite 完成检查，不修改演示服务中的原始故障样例。

| 场景 | 结果 |
| --- | --- |
| 批准 | 原始 Java 测试失败，确认前源码不变，错误摘要返回 409；正确批准后 COMPLETED，定向测试通过 |
| 恢复与重复确认 | 重建服务对象后从 SQLite 找回待审批任务；重复确认返回同一结果，不重写文件、不重跑 Java |
| 拒绝 | CANCELED，源码不变 |
| 源码改变后确认旧提案 | RESULT_UNKNOWN，保留外部改动，不执行测试，不自动重试 |
| 汇总 | 3 个场景，26 项断言通过，0 失败；外部模型请求 0 |

另运行真实 Docker 隔离探针与 Skill 加载回归，合计 14 passed、0 failed、0 skipped。探针验证独立测试容器；不是依靠演示 API 容器挂载 Docker Socket 执行。

## 复现

在已安装项目依赖并提供 JDK 17 的环境，从仓库根目录运行：

```text
python -m scripts.deployment_acceptance_20261009 --fixtures evaluation_fixtures --output reports/deployment-local.tmp.json
```

仅用于本仓库可信虚构夹具；脚本显式使用本地 JDK 运行器，不用于未知仓库。真实容器检查遵循开发回归 Skill 的 opt-in 流程：准备可信镜像，设置 `TRACEPILOT_DOCKER_TESTS=1`，运行 `tests/test_sandbox_integration.py`；Skill 回归入口是 `tests/test_skill_loading.py`。

## 能力边界

- 上述 26 项是固定离线链路验证，不是真实模型自主修复率；恢复范围是服务对象重建，不是进程崩溃故障注入。
- 默认模型工作流使用独立 Docker 测试运行器；此演示 Compose 为固定可信样例显式选择容器内本地 JDK。二者不可混称。
- 演示 Compose 未挂载持久数据卷：停止再启动同一容器可保留可写层，删除或重建容器不保证演示任务数据保留。
- 单实例内的锁和执行回执不等于多实例分布式写入保证。
