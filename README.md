# FengTang(冯唐)

> [English](README_EN.md) | 中文

**纯 Python 命令行邮件客户端 —— 收、发、读、搜、标记 —— 内置邮件服务器,并提供完整的智能体(function-calling)API。**

GPL-3.0-or-later 许可。零运行时依赖:SMTP、IMAP、POP3 客户端,asyncio 实现的 SMTP+POP3 服务器,SQLite 本地存储,MIME 解析,以及 NTLM 所需的 DES/MD4 等全部 SASL 认证,均基于 Python 标准库自行实现。

## 功能特性

- **全部主流协议**:SMTP(发送)、IMAP4(收取/搜索/标志/文件夹)、POP3(收取)——全面支持 SSL 与 STARTTLS。
- **全部主流认证方式**:SASL `PLAIN`、`LOGIN`、`CRAM-MD5`、`XOAUTH2`/`OAUTHBEARER`、`NTLM`(内置纯 Python DES + MD4),以及 POP3 的 `APOP`。`auto` 模式按服务器通告自动选择。
- **服务商预设**:gmail、outlook、qq、163、126、yahoo、icloud、zoho、aliyun、sina——一个参数填好全部服务器与端口。
- **内置邮件服务器**:没配服务器?`fengtang serve` 即可启动本地 SMTP + POP3 服务器(asyncio、零系统依赖),支持按邮箱认证、CRAM-MD5/APOP,并带中继防护。
- **本地 SQLite 存储**:每封收取/发出的邮件都可离线搜索、标记、移动、删除。
- **MIME 处理完善**:multipart/alternative、RFC 2047 中文头、附件(列出 + 保存)、HTML→纯文本兜底。
- **智能体 API**:每个操作都有对应的 OpenAI function-calling `TOOLS` schema + `dispatch()`,LLM 智能体能像人一样收发、搜索、标记邮件。

## 安装

要求 Python ≥ 3.10。运行时无任何第三方依赖,全部标准库。

```bash
# 推荐:直接装入现有 conda 环境(不需要 venv)
conda activate dev
pip install -e .[dev]        # [dev] 只额外装 pytest/ruff/mypy
```

只使用 CLI/API 的话,`pip install -e .` 即可。

## 数据与文件位置

所有运行时数据集中在**一个目录**:默认 `~/.fengtang/`。

| 文件 | 用途 | 权限 |
|---|---|---|
| `~/.fengtang/config.json` | 账号凭据(邮箱、授权码/token、服务器地址、端口) | `0600`(保存时强制,仅属主可读写) |
| `~/.fengtang/fengtang.db` | SQLite 存储:每封收取/发出的邮件——原始 RFC822 报文、解析后的头/正文、标志、附件 | `0600`(首次运行后收紧) |

目录解析优先级(从高到低):

1. `config.json` 内的 `data_dir` 字段
2. 环境变量 `FENGTANG_DATA_DIR`
3. 默认 `~/.fengtang/`

邮件数据库同时保存**完整原始报文**与解析后的字段(主题、发收件人、日期、标志、text/html 正文),因此可离线搜索,附件可随时重新提取。任何真实凭据或邮件内容都不会出现在代码、测试或文档中。

整体迁移(config + 邮件):

```bash
export FENGTANG_DATA_DIR=/Volumes/SecureUSB/fengtang
```

或在配置文件里设 `"data_dir": "/path"`。

### 配置账号(服务商预设)

```bash
fengtang config add qq you@qq.com --password <SMTP授权码> --auth login --provider qq
fengtang config test -a qq      # 探测 IMAP + POP3 + SMTP 认证
```

### 交互式 OAuth 登录(Gmail / Outlook)

Gmail、Outlook 这类 XOAUTH2 账号无需手动找 token,一条命令走浏览器授权:

```bash
# 推荐:引导式一键配置(打开控制台教你建 client_id,粘贴后自动继续登录)
fengtang setup-gmail you@gmail.com

# 已有 client_id 时,直接登录
fengtang config login you@gmail.com --provider gmail --client-id <id>
fengtang config login -a 已有账号名          # 对已有账号重新授权
```

Google 已不允许 Gmail scope 使用公共 client_id,因此需要你自己建一个
(桌面应用类型,无需审核,自己的账号即可用)。

浏览器打开 Google/Microsoft 授权页 → 登录 → 本地回环端口自动接收跳转,
access/refresh token 落盘 `~/.fengtang/config.json`(0600)。access token
过期会在下次使用时自动用 refresh token 续期。

### 收取、列览、阅读、搜索、标记

```bash
fengtang fetch -a qq -n 20      # 拉取新邮件进本地库
fengtang list --unread          # 按时间倒序
fengtang search "关键词"
fengtang read 42                # 头部 + 正文
fengtang read 42 --save-attachments ./att
fengtang mark 42 --flags seen,flagged
fengtang folders
```

### 发送

```bash
fengtang send -t bob@example.com -s "你好" -m "正文" \
    --attach ./report.pdf:report-2026.pdf
```

### 运行内置服务器

```bash
# 无认证模式,localhost,存入 ~/.fengtang/fengtang.db
fengtang serve --smtp-port 2525 --pop-port 1110 --domain localhost

# 带认证的邮箱(--user 可重复)
fengtang serve --user alice@localhost:secret1 --user bob@localhost:secret2
```

随后任何 SMTP/POP3 客户端都可连 `127.0.0.1:2525` / `127.0.0.1:1110`。本地域投递无需认证;向其他域中继需要认证,否则拒绝(550)。

### 脚本化 JSON 输出

所有命令都接受 `--json`,统一返回 `{"success", "data", "error", "metadata"}`:

```bash
fengtang list --json --unread | jq '.data[0].subject'
```

## 智能体集成(OpenAI Function Calling)

```python
from fengtang.agent.tools import TOOLS, dispatch

# 1. 把 TOOLS 加进模型的工具列表。
# 2. 模型发起工具调用时路由:
result = dispatch("fengtang_send", {
    "to": ["bob@example.com"],
    "subject": "智能体发来的问候",
    "body": "通过 fengtang 工具调用发送。",
})
print(result)  # {"success": True, "data": {...}, "error": None, "metadata": {...}}
```

可用工具:`fengtang_send`、`fengtang_list`、`fengtang_read`、`fengtang_search`、`fengtang_mark`、`fengtang_delete`、`fengtang_move`、`fengtang_fetch`、`fengtang_folders`、`fengtang_account_add`、`fengtang_account_list`、`fengtang_account_test`。

查看 schema:`fengtang api --schema`。

## Python API

```python
from fengtang import ToolResult, send_mail, list_messages, read_message, search_messages, mark_messages

result = send_mail(to=["bob@example.com"], subject="Hi", body="Hello")
if result.success:
    print(result.data["message_id"])
```

所有 API 函数返回 `ToolResult` dataclass(`success`、`data`、`error`、`metadata`、`.to_dict()`、`__bool__` 真值判断)。

## 配置

账号存于 `~/.fengtang/config.json`(0600)。完整布局与迁移方法见上文**数据与文件位置**。运行期覆盖:

- `FENGTANG_DATA_DIR` — 数据目录
- `fengtang serve --db <path>` — 内置服务器的自定义数据库路径

## 项目结构

```
fengtang/
├── core/            # config(账号/预设)、errors(ToolResult)、
│                    # auth(PLAIN/LOGIN/CRAM-MD5/XOAUTH2/NTLM/APOP)、
│                    # _des + _md4(NTLM 所需纯 Python 密码学原语)
├── mail/            # smtp_client、imap_client、pop_client、
│                    # parser(MIME 构建/解析/渲染)、store(SQLite)
├── serve/           # 内置 asyncio SMTP + POP3 服务器
├── agent/           # OpenAI function-calling TOOLS + dispatch
└── cli/             # argparse CLI(config/send/fetch/list/read/search/…)
tests/               # pytest 套件,含客户端↔内置服务器端到端回环
```

## 开发

```bash
conda activate dev      # 用现有环境,无需 venv
pip install -e .[dev]
pytest                  # 59 个测试
ruff check . && ruff format .
mypy fengtang
```

## 备注

- QQ/163/126 邮箱使用"授权码"而非登录密码——通过 `--password` 传入。凭据**只**存于 `~/.fengtang/config.json`(0600),绝不落入代码、测试或文档。
- XOAUTH2 token 可用 `fengtang config test -a acct --oauth2-token <token>` 或 `Account.oauth2_token` 提供(Gmail/Outlook OAuth 流程)。
- 内置服务器把投递的邮件存进同一个 SQLite 库,因此 `fengtang fetch --protocol pop3` 可以对它完整回环——测试智能体时不必碰真实邮箱。
- 开发直接使用 conda 环境(如 `conda activate dev && pip install -e .[dev]`)——无需 virtualenv。

## 许可

GPL-3.0-or-later(见 [LICENSE](LICENSE))。