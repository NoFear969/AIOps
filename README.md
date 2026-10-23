# logagg — 日志报错近实时归集（Slice 1）

经 **SFTP / 本地目录** 按字节水位增量拉取 `{LOG_ROOT}/{系统}-{YYYYMMDD}` → 解析 Java 多行堆栈并算指纹 → **Analyzer**（默认 `StubAnalyzer`）→ 本机 **MySQL** → 公司内部 SMTP（可 skipped）。

方案见项目文档；本仓库是第一个能跑通的切片，**不编造** WorkBuddy 聊天 HTTP schema，也不编造 SMTP 主机名。

## 环境要求

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- 本机 MySQL（联调入库时）；纯解析 / 水位单测不需要 MySQL

```bash
uv sync
cp .env.example .env   # 按需填写，勿提交密钥
```

## 环境变量

见 [`.env.example`](./.env.example)。要点：

| 变量 | 说明 |
| --- | --- |
| `LOG_SOURCE` | `local`（CI/fixture）或 `sftp`（生产） |
| `LOG_ROOT` | 日志根目录，文件为 `{system}-{YYYYMMDD}` |
| `SYSTEMS` | 逗号分隔系统名 |
| `TZ` | 默认 `Asia/Shanghai`（决定「今天」） |
| `ANALYZER` | `stub`（默认）或 `workbuddy` |
| `MYSQL_*` | 本机库连接 |
| `SFTP_*` | 生产拉取 |
| `SMTP_*` / `MAIL_*` | 内部邮件；缺 `SMTP_HOST` → `skipped_no_smtp` |

`ANALYZER=workbuddy` 但缺 Agent Key / CAM：水位仍前进，`analysis_status=skipped_no_creds`，不发原因邮件。

## MySQL schema

```bash
# 需本机 mysqld 已启动，且 .env 中 MYSQL_* 可用
uv run python scripts/bootstrap_db.py
# 或：mysql -uroot -p < sql/schema.sql
```

表：`watermarks`、`error_events`、`error_clusters`、`analyses`、`cluster_causes`、`notifications`。

提交顺序：**业务表 → 水位 → 通知意图**（水位后写，避免丢事件）。

## 本地 fixture / dry-run（无需 SFTP、可不写库）

```bash
# 准备当天文件（Asia/Shanghai 的 YYYYMMDD）
TODAY=$(TZ=Asia/Shanghai date +%Y%m%d)
mkdir -p fixtures/logs
cp tests/fixtures/java_stack_sample.log "fixtures/logs/checkout-${TODAY}"

export LOG_SOURCE=local LOG_ROOT=./fixtures/logs SYSTEMS=checkout ANALYZER=stub
uv run python -m logagg.poll --dry-run

# 再追加一行 ERROR，确认只消费新字节（联调 MySQL 时看 watermarks.byte_offset）
echo "2026-10-23 12:00:00 ERROR appended boom" >> "fixtures/logs/checkout-${TODAY}"
uv run python -m logagg.poll --dry-run
```

生产路径：`LOG_SOURCE=sftp`，配置 `SFTP_*` 后同样执行 `python -m logagg.poll`（不要 `--dry-run`）。

## 定时

本机 cron / systemd timer 约每 5 分钟：

```bash
*/5 * * * * cd /path/to/logagg && uv run python -m logagg.poll >>/var/log/logagg.poll.log 2>&1
```

## 测试

```bash
uv run pytest -q
```

覆盖：Java 多行栈折叠、相同堆栈同一 fingerprint、`byte_offset` / `last_partial` 跨轮次、本地 `LogSource` 只读新字节、stub 不发原因邮件、workbuddy 无凭证返回 `None`。

## Slice 1 验收对照

| 项 | 状态 |
| --- | --- |
| append ERROR 后水位只前进、不重扫全文 | 单测 `test_local_source_only_reads_new_bytes` + dry-run |
| 半行跨轮次由 `last_partial` 拼接 | `test_last_partial_joins_across_rounds` |
| `at ...` / `Caused by:` 折叠；同栈同指纹 | `test_parse_java_multiline_fixture` |
| 无 WMA 凭证：stub 入库、不发原因邮件 | 默认 `ANALYZER=stub`；`should_send_cause_email` |
| `ANALYZER=workbuddy` 缺密钥：水位前进、`skipped_no_creds` | `poll._analyze` |
| 未配 `SMTP_HOST`：入库、通知 skipped | `Notifier.plan_notifications` |
| WorkBuddy 真实 HTTP | **未做**（`workbuddy.py` 为 NotImplemented + OpenAPI TODO） |

## 包布局

```
src/logagg/
  poll.py         # 入口
  source.py       # local | sftp 增量读 + 水位纯函数
  parse.py        # Java 多行事件 + fingerprint
  analyze.py      # Analyzer 接口
  stub.py         # StubAnalyzer
  workbuddy.py    # 占位，待 OpenAPI 核对
  store.py        # MySQL
  notify.py       # 内部 SMTP
  config.py       # 环境变量
sql/schema.sql
```

## 明确不做（本切片）

- 真实 WorkBuddy 发消息 HTTP
- 公司 SMTP 真实凭证
- Redis / K8s / 仪表盘 / BDK
