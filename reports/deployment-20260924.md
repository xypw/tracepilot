# 2026-09-24 本机离线部署验收

使用已有 Python 虚拟环境和 JDK 启动 FastAPI，仅绑定 `127.0.0.1:8020`，入口为 `http://127.0.0.1:8020/demo`。规划器为 offline，外部模型请求为 0。未下载新的 Docker 基础镜像；Compose 端口配置也已收紧为本机绑定。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 健康、演示页、OpenAPI | HTTP 200 |
| 原始故障 | 真实 javac/java 重现测试失败 |
| 等待批准 | WAITING_APPROVAL，正式工作区副本未改动 |
| 错误提案摘要批准 | HTTP 409，文件未改动 |
| 正确批准 | COMPLETED，修复后目标测试通过 |
| 重复批准 | 返回相同结果，源文件修改时间未改变 |
| 保存结果读取 | GET 返回 COMPLETED |
| 本机 pytest | 30/30 通过 |

操作对象为 `null-customer-name` 的临时副本，不修改版本库内原始 fixture。该结果验证离线执行及审批链路，不提供真实模型修复率。

## 复现

将 `evaluation_fixtures/null-customer-name` 复制到新的工作区副本，按 README 的本地启动步骤设置 `TRACEPILOT_WORKSPACE_ROOT`、独立的 `TRACEPILOT_STATE_DIR`、`TRACEPILOT_PLANNER=offline` 和 Java 路径。服务启动后执行：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_deployed_runtime.py --workspace <工作区副本> --allow-demo-write
```

脚本会修改该副本完成修复。再次测试应使用新的副本及对应服务配置；不能用已经修复的副本声称重现了原始故障。部署脚本只用于可信测试仓库，临时目录不是操作系统安全沙箱。
