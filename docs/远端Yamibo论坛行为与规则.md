# 远端 Yamibo 论坛行为与规则（经验迁移）

> 本文面向需要对 Yamibo 论坛进行二次开发的其他项目。
>
> 内容提取自本项目对远端 `https://bbs.yamibo.com` 的请求、解析、测试和历史实测，重点描述远端论坛的可观察行为，不把本项目的数据库、缓存、前端筛选或任务调度策略当作 Yamibo 官方规则。
>
> 观察日期：2026-08-28。WAF、页面结构、插件和分区都可能变化；上线前应使用实际账号和目标页面重新验证。

## 1. 核心结论

| 优先级 | 可迁移结论 | 置信度 |
| --- | --- | --- |
| 1 | Yamibo 的主要访问面是 Discuz 风格 HTML 页面，不能只依靠 HTTP 状态码判断成功。 | 高 |
| 2 | 登录状态、阅读权限、论坛维护、主题删除、反爬拦截必须分开建模。 | 高 |
| 3 | 主题和帖子应分别使用 `tid`、`pid` 作为稳定身份；附件不能使用完整签名 URL 作为永久 ID。 | 高 |
| 4 | 代理可用不代表目标主题可访问，必须验证实际的论坛列表或主题详情请求。 | 高 |
| 5 | WAF、验证码、Cookie 过期和维护窗口会使同一个 URL 返回完全不同的 HTML。 | 高 |
| 6 | 请求频率、分页上限和并发量需要保守控制；本项目中的具体数值属于经验值，不是官方限速承诺。 | 中 |

## 2. 远端站点和 URL 形态

### 2.1 主域名和常见 URL

远端站点主域名是：

```text
https://bbs.yamibo.com
```

常见 URL 形态包括：

```text
/forum.php?mod=viewthread&tid=<tid>
/forum.php?mod=viewthread&tid=<tid>&page=<page>
/forum-<fid>-<page>.html
/forum.php?mod=forumdisplay&fid=<fid>&orderby=dateline&page=<page>
```

项目中将 `forum.php?mod=viewthread&tid=<tid>` 作为主题的规范 URL，同时兼容 Discuz 的伪静态主题链接和分区链接。页面编号从 `1` 开始，不能把 `0` 当作第一页。

证据：[urls.py](../src/yamibo_mcp/yamibo/urls.py#L11)。

### 2.2 资源身份

建议把远端资源抽象为以下身份层级：

```text
论坛：fid
主题：tid
帖子/楼层：pid
附件：aid
帖子内图片位置：pid + image_index
```

`floor_no` 可以作为本地展示序号，但不应替代远端 `pid`。本项目的 `floor_no` 是按解析顺序生成的本地字段，不是已确认的远端数据库字段。

### 2.3 已观察到的分区

本项目配置过的常用分区包括：

| `fid` | 名称 |
| ---: | --- |
| 30 | 漫画区 |
| 55 | 轻小说区 |
| 5 | 动漫区 |
| 33 | 海域区 |
| 13 | 贴图区 |
| 16 | 管理版 |
| 19 | 资源交流区 |
| 44 | 游戏区 |
| 49 | 文学区 |
| 370 | 使用指南 |
| 379 | 影视区 |

这不是官方完整分区目录，只是当前项目观察到的映射。迁移项目不应假定分区 ID 永久不变，也不应把 `content_kind` 等本地分类当作远端字段。

证据：[forums.py](../src/yamibo_mcp/domain/forums.py#L6)。

## 3. 页面结构和数据语义

### 3.1 论坛列表页

正常论坛列表页通常能看到：

- `id="threadlisttableid"`；
- 置顶主题行 `stickthread_*`；
- 普通主题行 `normalthread_*`；
- 主题标题链接 `class="s xst"`；
- 主题分类、发布者、发布时间；
- 最后回复者、最后回复时间；
- 回复数和浏览数。

分页信息通常从 `共 N 页` 或最后一页链接中提取，不能写死页数。

证据：[forum_list.py](../src/yamibo_mcp/yamibo/parsers/forum_list.py#L11)。

置顶主题是远端列表中的一种行类型；“默认隐藏置顶”或“隐藏标题含公告的主题”属于本项目的展示策略，不是远端访问规则。

### 3.2 主题详情页

正常主题详情页通常包含：

- 标题：`id="thread_subject"`；
- 帖子正文：`id="postmessage_<pid>"`；
- 帖子容器：`post_<pid>`；
- 作者、用户 UID 和发表时间；
- 引用、链接、格式化文本和图片。

帖子应以 `pid` 作为稳定标识；页面中的顺序可用于生成本地展示序号，但不能仅凭序号跨页或跨次抓取识别同一个帖子。

证据：[thread_detail.py](../src/yamibo_mcp/yamibo/parsers/thread_detail.py#L74)。

### 3.3 搜索页

论坛搜索具有 Discuz 的状态依赖，常见流程是：

1. 先打开论坛页；
2. 提取动态 `formhash`；
3. POST 到 `search.php?searchsubmit=yes`；
4. 携带 `mod=curforum`、`srchtype`、`srhfid`、`srchtxt` 等字段；
5. 使用同源 `Referer` 和当前会话解析结果。

因此不能长期硬编码 `formhash` 或假定搜索表单字段永远不变。搜索结果通常包含主题标题、发布者、时间、分区、回复数和摘要。

证据：[search_results.py](../src/yamibo_mcp/yamibo/parsers/search_results.py#L11)、[client.py](../src/yamibo_mcp/yamibo/client.py#L515)。

## 4. 登录、Cookie 和阅读权限

### 4.1 Discuz 登录流程

项目观察到的登录流程为：

```text
GET  /member.php?mod=logging&action=login
POST /member.php?...loginsubmit=yes
```

登录页中的 `formhash` 和表单 action 是动态的，应从每次获取到的登录页解析。Cookie 应属于 `bbs.yamibo.com` 域名，并在后续列表、搜索、主题和附件请求中复用同一会话。

证据：[client.py](../src/yamibo_mcp/yamibo/client.py#L900)。

Cookie 存在或请求返回 HTTP 200，都不能单独证明登录成功。至少还应确认页面不是登录页，并存在已登录标记、退出入口或正常目标页面结构。

### 4.2 权限状态

需要区分：

- `您尚未登录`：会话无效或未登录；
- `没有权限访问该版块`：分区访问受限；
- `阅读权限高于 X`：当前主题要求更高阅读权限；
- `您需要升级您所在的用户组`：用户组权限不足。

当页面明确给出 `阅读权限高于 X` 时，`X` 是远端页面给出的权限阈值。迁移项目应记录该阈值，并选择实际权限更高的账号；重复使用同一账号重试没有意义。

证据：[page_classifier.py](../src/yamibo_mcp/yamibo/page_classifier.py#L1)、[test_page_classifier.py](../tests/unit/test_yamibo/test_page_classifier.py#L6)。

### 4.3 每日签到插件

项目观察到当前站点存在签到插件：

```text
/plugin.php?id=zqlj_sign
```

页面语义包括：

- `今日已打卡`：当天已经签到；
- `点击打卡`：可以执行签到；
- `恭喜您，打卡成功`：明确表示成功。

签到动作链接可能包含动态参数，不能只依赖固定 URL 或固定 `formhash`。这是插件行为，可能随站点更新变化，应视为时间敏感能力。

证据：[client.py](../src/yamibo_mcp/yamibo/client.py#L154)。

## 5. 维护、WAF 和反爬行为

### 5.1 页面和响应特征

项目实际遇到或专门处理过以下情况：

- 维护页：`百合会每日维护`、`alt="每日维护"`；
- 百度 WAF：HTTP 405、`server: BAIDU_WAF`、`__noxExpire`、`gangplank_`；
- Nox JS 挑战：`nox_jst_v1`；
- Cloudflare 类挑战：`acw_sc__v2`；
- HTTP 429 限流；
- HTTP 444、连接重置、HTTP/2 协议错误；
- HTTP 200，但正文实际是登录页、验证码页或 WAF HTML。

WAF 挑战页面不是正常论坛页面。迁移项目应先分类页面，再进入列表、搜索或主题解析。

证据：[anti_bot.py](../src/yamibo_mcp/yamibo/anti_bot.py#L47)、[waf_challenge.py](../src/yamibo_mcp/yamibo/waf_challenge.py#L1)、[cf_challenge.py](../src/yamibo_mcp/yamibo/cf_challenge.py#L1)。

### 5.2 代理和健康检查

代理节点的健康状态只能说明某一次探测路径可达，不能证明以下条件都满足：

- 目标主题可访问；
- Cookie 有效；
- 当前请求的 headers/TLS 指纹能通过 WAF；
- 当前账号具有目标主题权限；
- 返回页面属于预期类型。

因此健康检查和真实的主题请求必须分开判断。中立网站可用于判断网络是否连通，但不能代替 Yamibo 目标路径检查。

### 5.3 可迁移的保守请求基线

以下是本项目根据实际访问得到的客户端基线，不是远端官方限速值：

- 同一 Cookie 会话使用稳定 User-Agent；
- 携带合理浏览器 headers 和同源 `Referer`；
- 单个会话保持低并发；
- 普通请求之间保留间隔和随机抖动；
- 连续分页使用退避，并设置最大页数；
- 出现 WAF、429、444 或验证码后暂停，不要立即洪泛重试。

浏览器能够通过一次正常 WAF 挑战，不代表浏览器可以绕过验证码；浏览器 fallback 只是访问方式变化，不是权限提升。

## 6. 附件和图片规则

### 6.1 展示 URL 与下载 URL 不同

帖子中的图片可能通过 `src`、`data-src`、`file` 或 `zoomfile` 展示。真正下载时，页面还可能提供类似以下形式的附件链接：

```text
/forum.php?mod=attachment...&nothumb=yes
```

展示图片和完整附件下载链接需要通过 `aid` 及附件菜单映射，不能只读取一个 `<img>` 的 `src`。

证据：[_post_extract.py](../src/yamibo_mcp/yamibo/parsers/_post_extract.py#L107)。

### 6.2 签名 URL 不能作为永久身份

附件查询参数或签名可能轮换。因此：

- 使用 `aid` 作为附件的主要身份；
- 使用 `pid` 和 `image_index` 保存它在帖子中的位置；
- 将当前可用下载 URL 当作可更新的定位信息；
- 不要因为完整 URL 变化就判定附件消失。

### 6.3 图片成功条件

HTTP 200 不代表图片成功。应同时检查：

- Content-Type 是否为图片；
- 文件魔数是否符合图片格式；
- 文件是否可以实际解码；
- 响应体是否其实是登录页、WAF 或验证码 HTML。

下载附件时通常需要复用当前 Cookie、Referer、代理和浏览器 headers。站内附件与外部图片链接应分开建模，因为外部链接的可恢复性和认证方式不同。

证据：[images.py](../src/yamibo_mcp/storage/images.py#L499)。

## 7. 远端状态分类建议

| 页面/响应特征 | 远端含义 | 建议分类 |
| --- | --- | --- |
| `thread_subject`、`postmessage_<pid>` | 正常主题详情 | `normal` |
| `您尚未登录`、登录页标题 | 会话无效或未登录 | `login_required` |
| `阅读权限高于 X` | 主题阅读权限不足 | `permission_required(X)` |
| `您需要升级您所在的用户组` | 用户组不足 | `permission_required` |
| `指定的主题不存在...已被删除或正在被审核` | 主题缺失、删除或审核中 | `missing_or_review` |
| `本帖已经删除，错误权限代码255` | 明确删除状态 | `deleted` |
| `查无此区，此区已关闭` | 分区不存在或关闭 | `forum_closed` |
| `百合会每日维护` | 站点维护 | `maintenance` |
| `BAIDU_WAF`、`__noxExpire`、`nox_jst_v1`、`acw_sc__v2` | WAF 或反爬挑战 | `soft_blocked` |
| HTTP 429 | 限流 | `rate_limited` |
| HTTP 444、连接重置、未知挑战页 | 访问被拦截或传输异常 | `soft_blocked` / `transport_error` |
| HTTP 200 且响应是 WAF/挑战页 | 图片请求被反爬页面拦截 | `waf_response` |
| HTTP 200 但图片响应是普通 HTML | 图片请求返回网页而非图片 | `html_response` |
| HTTP 200 且带图片头但文件不完整 | 图片体被截断或传输不完整 | `truncated_image` |
| HTTP 200 但既不是图片也不是 HTML | 图片响应体无效 | `invalid_image_body` |

其中删除、权限不足、分区关闭属于业务语义，不应通过无限更换代理重试。WAF、限流和传输异常才适合进入有上限的等待、退避或恢复流程。

完整页面分类实现和测试见：[page_classifier.py](../src/yamibo_mcp/yamibo/page_classifier.py#L1)、[test_page_classifier.py](../tests/unit/test_yamibo/test_page_classifier.py#L6)。

## 8. 不应误迁移为远端规则的内容

以下内容是本项目自身的实现选择，不代表 Yamibo 官方规则：

- 默认优先使用 `fid=30`；
- 默认隐藏置顶主题或标题含“公告”的主题；
- 漫画、轻小说、动漫等 `content_kind` 分类；
- 缓存时长、最大抓取页数、重试次数和具体 cooldown；
- 本地生成的 `floor_no`；
- `permission=0` 等本地默认值；
- 图片文件名、目录结构和本地排序；
- 浏览器 fallback、代理轮换和 Job 状态字段。

尤其是楼层编号，应优先保留 `pid`，将 `floor_no` 视为本地展示信息。

## 9. 尚未确认的远端能力

仅凭当前项目不能确认以下内容：

- 是否存在稳定、公开且长期兼容的 JSON API；
- 当前所有分区的完整目录和权限矩阵；
- 精确限速阈值；
- 论坛实际每页主题数量；
- WAF 挑战规则是否长期不变；
- 发帖、回帖、编辑、删除等写操作的完整协议；
- 当前验证码类型和人工处理要求。

这些内容需要使用实际账号、实际目标分区和实际目标主题进行现场验证。

## 10. 推荐的远端抽象模型

迁移到其他项目时，可以把 Yamibo 的远端接口抽象成：

```text
Resource identity:
  fid / tid / pid / aid

Access state:
  normal
  login_required
  permission_required(level)
  deleted
  under_review
  forum_closed
  maintenance
  rate_limited
  soft_blocked
  transport_error
  unknown

Success condition:
  HTTP/传输成功
  + 页面类型正确
  + 登录/权限语义正确
  + 内容类型正确
```

核心原则是：不要把“请求返回了页面”“代理健康”“HTTP 200”直接等同于“远端数据获取成功”。
