# 账号池与自动登录设计

> 版本：0.9.3 | 更新日期：2026-06-26

本文定义 Yamibo 远端抓取的第一阶段账号池方案。目标不是立刻做复杂分布式调度，而是在保持单账号兼容的前提下，引入多账号配置、自动登录和 cookie 自动持久化。

## 1. 目标

- 从配置文件读取多个论坛账号。
- 每个账号拥有独立 cookie 文件。
- 远端抓取前优先复用已有 cookie。
- cookie 不可用时，使用配置中的账号密码自动登录。
- 登录成功后自动保存 cookie。
- 在单进程内支持基于账号的轻量轮转，为后续多 worker 并发打基础。

## 2. 配置模型

新增 `yamibo.account_pool`：

```json
{
  "yamibo": {
    "account_pool": [
      {
        "account_id": "primary",
        "username": "user_a",
        "password": "pass_a",
        "cookie_file": "data/cookies/primary.cookie",
        "enabled": true,
        "weight": 2,
        "request_interval_seconds": 1.0,
        "request_interval_jitter_seconds": 0.5,
        "max_concurrent_leases": 1,
        "login_mode": "refresh_on_login_required"
      }
    ]
  }
}
```

兼容规则：

- 未配置 `account_pool` 时，继续使用现有单账号字段：
  - `yamibo.cookie_file`
  - `login.username`
  - `login.password`
- 配置 `account_pool` 后，daemon 远端抓取优先使用账号池。

## 3. 核心模型

### 3.1 AccountIdentity

运行时身份包含：

- `account_id`
- `username`
- `password`
- `cookie_file`
- `enabled`
- `weight`
- `request_interval_seconds`
- `request_interval_jitter_seconds`
- `max_concurrent_leases`
- `login_mode`

### 3.2 AccountPool

第一阶段为进程内轻量池，职责：

- 从可用账号中选择一个身份。
- 在单进程内维护 `inflight` 计数。
- 按轮转顺序分散请求。
- 在没有配置池时自动退化到单账号默认身份。

## 4. 请求流程

每次借出账号后：

1. 读取该账号的 `cookie_file`
2. 用该账号创建 `YamiboClient`
3. 远端请求优先走现有 cookie
4. 如果返回登录页，使用该账号的用户名和密码执行登录
5. 登录成功后自动保存 cookie

关键约束：

- 一个账号对应一个 cookie 文件
- 不同账号不得共用 cookie 文件
- 一个抓取流程内尽量固定使用同一账号

## 5. 当前接入范围

第一阶段已优先接入：

- daemon `sync_thread`
- daemon `update_thread`

原因：

- 这些路径最容易受论坛登录态和吞吐影响
- 它们覆盖了大多数真实远端抓取工作流
- `application/*` 只读查询暂时保持原有单账号构造方式，以避免扩大兼容面

## 6. 非目标

第一阶段暂不解决：

- 多进程 / 多 daemon 之间共享账号租约
- 基于数据库的账号健康度与熔断状态
- 单贴内部 aggressive 并发抓取
- 自动验证码 / 二次验证处理

这些属于第二阶段。

## 7. 后续演进

第二阶段建议新增 SQLite 持久化状态表，记录：

- `account_id`
- `inflight_count`
- `failure_count`
- `cooldown_until`
- `last_success_at`
- `last_error_code`

这样多 worker 才能真正共享账号健康和租约状态。
