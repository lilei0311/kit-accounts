# 双账 A–F 本地联调（审查用，非合闸）

Hub 业务 API 写 outbox，再 `POST /v1/ops/dispatch-points-outbox` 真 HTTP 推到本仓
`POST /internal/v0/meter-events`。同 `request_id` 对账。不部署、不接真扣费、
不改两仓业务代码、不在 kit-meter 开 PR、不合 kit-meter PR #9。

## 怎么跑（TechBot 固定版本）

干净同机：各自项目 `.venv`，不要父 `VIRTUAL_ENV` / `PYTHONPATH` / `PYTHONHOME`，
不要 `sitecustomize` 补丁。kit-meter 必须已在**完整固定 SHA**（TechBot 给出；
默认锁 `40967aae` = [kit-meter PR #9](https://github.com/lilei0311/kit-meter/pull/9)，
不要合）。`KIT_METER_REPO_ROOT` 的 HEAD 必须与 pin 一致；不一致会报错，
**不会**悄悄 checkout 到旧 pin。不要无约束跟随最新分支。

```bash
unset VIRTUAL_ENV PYTHONPATH PYTHONHOME PYTHONSTARTUP UV_PROJECT_ENVIRONMENT
export POINTS_METER_CALLBACK_TOKEN=local-only-meter-callback
export POINTS_METER_CALLBACK_CREDENTIAL_ID=cred_local_points_meter
export KIT_METER_PIN=<kit-meter 完整固定 SHA>   # 例：40967aae… ；短锁默认 40967aae
export KIT_METER_REPO_ROOT=/path/to/kit-meter     # HEAD 必须匹配 PIN
# 仅当操作员明确要把 ROOT 拨到 PIN 时才开：
# export KIT_METER_CHECKOUT_PIN=1
cd /path/to/kit-accounts   # 本 PR 的锁定 HEAD
uv run python scripts/joint_debug/run_af.py
```

主入口是 `run_af.py`：起本地 Postgres（积分 schema `points` + 计量抛掷库）、
积分入站（优先 PG）、校验 kit-meter HEAD 与 pin、隔离 venv 起 Hub、跑 A–F、写证据。

`meter-hub-joint-debug-af.sh` 是计量侧预备泵的拷贝；对账报告仍以 Python 跑次为准。

## E1 / E2（必须分两项留证）

| 例 | 模式 | 门禁 |
|---|---|---|
| E1 | Hub 端到端 | 无 hold 先 release → 积分 `cancelled_without_hold`、`entries=[]`、outbox acked；终态后再 hold 必须 **Hub HTTP 409**（不可用积分 rejected 凑绿） |
| E2 | 本地传输模拟（非 Hub 端到端） | 直发积分 `POST /internal/v0/meter-events`，同 rid、原 `evt_hold_<rid>`、原 account/billing/estimate（用量十进制字符串）、本地假凭据；`result=rejected` 且无新扣款。**E1 的 409 不能替代 E2** |

A/B/C/D/F 保持原验收强度。D 必须 `duplicate` + `original_result` + 原 `snapshot_ref`。
C 拒后不放行模型、不自造 release。F shortfall 停重试。

## 夹具账户（仅本机）

| account_id | 积分 | 用于 |
|---|---|---|
| `acct_joint_debug` | 1_000_000 | A/B/D/E1/E2 |
| `acct_joint_debug_poor` | 1 | C 大额 hold → rejected |
| `acct_joint_debug_short` | 5 | F hold 4pt / settle 8pt → shortfall |
| `acct_demo` | 1_000_000 | 与薄壳文档兼容 |

费率：`RateTable.from_snapshot()` 的 `demo-text-v0`。

## 证据

- `docs/joint-debug-af-evidence.md` — A–F（含 E1/E2）PASS/FAIL + Hub 问题单（请求/回执原文）
- `docs/joint-debug-af/run.json` 与 `docs/joint-debug-af/evidence/*.json`

证据 JSON 里 Postgres Numeric 以十进制字符串写出，禁止 float。入站契约本来收
decimal 字符串，不为此改业务解析。

回调凭据只用 `POINTS_METER_*` / `KIT_METER_POINTS_CALLBACK_*`。客户 `msk_…` 仅 Hub
`/v1alpha/keys` 业务 API；**禁止**当作回调 Bearer。
