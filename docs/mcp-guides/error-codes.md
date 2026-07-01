# Error Codes

> 公共错误码、含义与修复建议。

## 错误码列表

| 错误码 | 含义 | 重试 | 修复方向 |
|---|---|---|---|
| `JOB_NOT_FOUND` | 任务不存在或已过期 | 否 | 检查 job_id 是否正确 |
| `REMOTE_FETCH_FAILED` | 远端论坛请求失败 | 是 | 网络恢复后重试；检查 Cookie 是否有效 |
| `REMOTE_LOGIN_REQUIRED` | 需要登录才能访问 | 否 | 确认 `.cookie` 文件有效；重新登录 bbs.yamibo.com |
| `REMOTE_MAINTENANCE` | 论坛正在维护 | 是 | 等待维护结束后重试 |
| `REMOTE_ACCESS_PAUSED` | 触发反爬保护，已自动暂停 | 是 | 等待冷却期结束后自动恢复 |
| `REMOTE_THREAD_PERMISSION_REQUIRED` | 帖子需要更高阅读权限 | 否 | 使用更高权限账号或配置账号池 |
| `UNEXPECTED_REMOTE_PAGE` | 页面类型与预期不符 | 否 | 检查 tid 是否正确；帖子可能已删除 |
| `EXPORT_PRECHECK_FAILED` | 导出前置检查失败 | 否 | 确认帖子有完整本地归档 |
| `LOCAL_ARCHIVE_NOT_FOUND` | 本地归档不存在 | 否 | 先创建归档任务 |
| `INVALID_ARGUMENT` | 参数无效 | 否 | 检查参数类型和范围 |
| `INTERNAL_ERROR` | 内部未预期错误 | 是 | 查看 daemon 日志排查 |

## 故障排查

### 查看任务失败原因

```bash
uv run yamibo-archiver job-status <job_id>
```

### 查看远程访问暂停状态

```bash
uv run yamibo-archiver read-resource "yamibo://system/remote-access-pause"
```

### 手动恢复远程访问

在 Web 控制台 `http://127.0.0.1:8765` 点击 "恢复远程访问"，或通过 API：

```bash
curl -X POST http://127.0.0.1:8765/api/remote-access/resume
```

### 检查 Daemon 日志

Daemon 日志包含任务执行的详细错误上下文。启动 daemon 时观察终端输出，或在 Web 控制台的 Logs 页面查看。
