# FengTang(冯唐)

> English | [中文](README.md)

**About the name:** “FengTang (冯唐)” echoes the ancient tale of *yun zhong
chuan shu* (云中传书) — the courier who carried letters through the clouds
and frontier posts. May every message you send with FengTang be carried as
faithfully as those old family letters.

**Pure-Python command-line mail client — send, fetch, read, search, mark — with a built-in mail server and a full agent (function-calling) API.**

GPL-3.0-or-later licensed. Zero runtime dependencies: everything (SMTP, IMAP, POP3 clients, an asyncio SMTP+POP3 server, SQLite storage, MIME parsing, SASL auth incl. DES/MD4 for NTLM) is built on the Python standard library.

## Features

- **All mainstream protocols**: SMTP (send), IMAP4 (fetch/search/flags/folders), POP3 (fetch) — SSL and STARTTLS everywhere.
- **All mainstream auth methods**: SASL `PLAIN`, `LOGIN`, `CRAM-MD5`, `XOAUTH2`/`OAUTHBEARER`, `NTLM` (with a pure-Python DES + MD4), and POP3 `APOP`. `auto` mode tries what the server advertises.
- **Provider presets**: gmail, outlook, qq, 163, 126, yahoo, icloud, zoho, aliyun, sina — one flag fills all host/port settings.
- **Built-in mail server**: no server configured? Run `fengtang serve` to start a local SMTP + POP3 server (asyncio, zero system dependencies) with optional per-mailbox auth, CRAM-MD5/APOP support and relay protection.
- **Local SQLite store**: every fetched/sent message is searchable, flaggable, movable, deletable — offline.
- **MIME done right**: multipart/alternative, RFC 2047 CJK headers, attachments (list + save), HTML→text fallback.
- **Agent API**: an OpenAI function-calling `TOOLS` schema + `dispatch()` for every operation, so an LLM agent can read/write/search/mark mail exactly like a human user.

## Installation

Requires Python ≥ 3.10. No runtime dependencies — everything is standard library.

```bash
# Recommended: install into your existing conda env (no venv needed)
conda activate dev
pip install -e .[dev]        # [dev] adds pytest/ruff/mypy only
```

`pip install -e .` alone is enough for CLI/API usage.

## Data & file locations

All runtime data lives in **one directory**: `~/.fengtang/` by default.

| File | Purpose | Permissions |
|---|---|---|
| `~/.fengtang/config.json` | Account credentials (email, auth code/token, server hosts, ports) | `0600` (owner-only, enforced on save) |
| `~/.fengtang/fengtang.db` | SQLite store: every fetched/sent message — raw RFC822 source, parsed headers/body, flags, attachments | `0600` (tightened after first run) |

Location resolution order (highest first):

1. `data_dir` field inside `config.json`
2. Environment variable `FENGTANG_DATA_DIR`
3. Default `~/.fengtang/`

The mail database stores the **full raw message** plus parsed fields
(subject, from/to, date, flags, text/html bodies), so searches work offline
and attachments can be re-extracted any time. No message content is kept
anywhere else — no code, no tests, no README ever contain real credentials
or message data.

To move everything (config + mail) elsewhere:

```bash
export FENGTANG_DATA_DIR=/Volumes/SecureUSB/fengtang
```

Or set `"data_dir": "/path"` in the config file.

### Credentials & security (important)

**Passwords live only in the local config — never in code, docs, tests, or git.**

Credentials are stored exclusively in `~/.fengtang/config.json` (mode `0600`). Three
out-of-band channels are supported, in priority order:

1. **Environment variable** (highest priority, nothing written to disk)
   ```bash
   export FENGTANG_PASSWORD_QQ=<your-auth-code>   # name = FENGTANG_PASSWORD_ + ACCOUNT (upper)
   fengtang fetch -a qq
   ```
2. **Password file** (secret kept in its own file, e.g. an encrypted volume)
   ```bash
   fengtang config add qq you@qq.com --password-file ~/.secrets/qq.txt
   ```
3. **Secure set** (no echo, no shell history, no chat transcript)
   ```bash
   fengtang config set-password qq            # interactive, confirmed twice
   echo "$PW" | fengtang config set-password qq --stdin   # pipe from a password manager
   fengtang config set-password qq --password-file ~/.secrets/qq.txt
   ```

`fengtang config add` without `--password` prompts with no echo. Prefer these
interactive/file/env forms so a secret never lands in a plaintext command line
or a pasted conversation.

### Configure an account (provider preset)

### Configure an account (provider preset)

```bash
fengtang config add qq you@qq.com --password <SMTP-auth-code> --auth login --provider qq
fengtang config test -a qq        # probes IMAP + POP3 + SMTP auth
```

### Interactive OAuth login (Gmail / Outlook)

XOAUTH2 accounts (Gmail, Outlook) need no manual token hunting — one command
runs the browser consent flow:

```bash
# Recommended: guided setup (opens the console, then continues into login)
fengtang setup-gmail you@gmail.com

# Or, with an existing client_id:
fengtang config login you@gmail.com --client-id <your-client-id>
fengtang config login -a <existing-account>   # re-authorize an account
```

Google no longer permits shared public client_ids for the Gmail scope, so you
create your own (Desktop app type — no verification review needed for your
own account).

The provider's consent page opens in your default browser; a temporary local
loopback port catches the redirect, then access/refresh tokens are persisted
to `~/.fengtang/config.json` (0600). An expired access token is refreshed
automatically with the stored refresh token on next use.

### Fetch, list, read, search, mark

```bash
fengtang fetch -a qq -n 20   # same as before        # pull new mail into local store
fengtang list --unread            # newest first
fengtang search "Foxmail"
fengtang read 42                  # headers + body
fengtang read 42 --save-attachments ./att
fengtang mark 42 --flags seen,flagged
fengtang folders
```

### Send

```bash
fengtang send -t bob@example.com -s "Hello" -m "Body text" \
    --attach ./report.pdf:report-2026.pdf
```

### Run the built-in server

```bash
# Open (no auth) on localhost, storing into ~/.fengtang/fengtang.db
fengtang serve --smtp-port 2525 --pop-port 1110 --domain localhost

# Auth-protected mailboxes (repeat --user)
fengtang serve --user alice@localhost:secret1 --user bob@localhost:secret2
```

Then point any SMTP/POP3 client at `127.0.0.1:2525` / `127.0.0.1:1110`. Local-domain delivery is accepted without auth; relaying to other domains requires authentication and is otherwise denied (550).

### JSON output for scripting

Every command accepts `--json` and returns `{"success", "data", "error", "metadata"}`:

```bash
fengtang list --json --unread | jq '.data[0].subject'
```

## Agent Integration (OpenAI Function Calling)

```python
from fengtang.agent.tools import TOOLS, dispatch

# 1. Pass TOOLS to your model's tool list.
# 2. When the model calls a tool, route it:
result = dispatch("fengtang_send", {
    "to": ["bob@example.com"],
    "subject": "Hi from the agent",
    "body": "Sent via fengtang tool call.",
})
print(result)  # {"success": True, "data": {...}, "error": None, "metadata": {...}}
```

Available tools: `fengtang_send`, `fengtang_list`, `fengtang_read`, `fengtang_search`, `fengtang_mark`, `fengtang_delete`, `fengtang_move`, `fengtang_fetch`, `fengtang_folders`, `fengtang_account_add`, `fengtang_account_list`, `fengtang_account_test`.

Inspect the schema yourself: `fengtang api --schema`.

## Python API

```python
from fengtang import ToolResult, send_mail, list_messages, read_message, search_messages, mark_messages

result = send_mail(to=["bob@example.com"], subject="Hi", body="Hello")
if result.success:
    print(result.data["message_id"])
```

All API functions return a `ToolResult` dataclass (`success`, `data`, `error`, `metadata`, `.to_dict()`, truthiness via `__bool__`).

## Configuration

Accounts live in `~/.fengtang/config.json` (0600). See **Data & file locations** above for the full layout and how to relocate it. Per-run overrides:

- `FENGTANG_DATA_DIR` — data directory
- `fengtang serve --db <path>` — custom database path for the built-in server

## Project structure

```
fengtang/
├── core/            # config (accounts/presets), errors (ToolResult),
│                    # auth (SASL PLAIN/LOGIN/CRAM-MD5/XOAUTH2/NTLM/APOP),
│                    # _des + _md4 (pure-Python crypto primitives for NTLM)
├── mail/            # smtp_client, imap_client, pop_client,
│                    # parser (MIME build/parse/render), store (SQLite)
├── serve/           # built-in asyncio SMTP + POP3 server
├── agent/           # OpenAI function-calling TOOLS + dispatch
└── cli/             # argparse CLI (config/send/fetch/list/read/search/…)
tests/               # pytest suite incl. end-to-end client↔built-in-server loops
```

## Development

```bash
conda activate dev      # your existing env; no venv needed
pip install -e .[dev]
pytest                  # 59 tests
ruff check . && ruff format .
mypy fengtang
```

## Notes

- QQ/163/126 mailboxes use "authorization codes" (授权码) instead of the account password — pass it via `--password`. Credentials are stored **only** in `~/.fengtang/config.json` (0600); they never appear in code, tests, or docs.
- XOAUTH2 tokens can be supplied with `fengtang config test -a acct --oauth2-token <token>` or `Account.oauth2_token` for Gmail/Outlook OAuth flows.
- The built-in server stores delivered mail in the same SQLite store, so `fengtang fetch --protocol pop3` round-trips against it — useful for testing agents without touching a real mailbox.
- Development uses a conda env directly (e.g. `conda activate dev && pip install -e .[dev]`) — no virtualenv required.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)).