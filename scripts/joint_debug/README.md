# 双账 A–F 本地联调（审查用，非合闸）

Hub 业务 API 写 outbox，再 `POST /v1/ops/dispatch-points-outbox` 真 HTTP 推到本仓
`POST /internal/v0/meter-events`。同 `request_id` 对账。不部署、不接真扣费、
不改两仓业务代码、不在 kit-meter 开 PR。

## 怎么跑

```bash
export POINTS_METER_CALLBACK_TOKEN=local-only-meter-callback
export POINTS_METER_CALLBACK_CREDENTIAL_ID=cred_local_points_meter
# 可选：已 checkout e80920eb 的 kit-meter 树
# export KIT_METER_REPO_ROOT=/path/to/kit-meter
uv run python scripts/joint_debug/run_af.py
```

主入口是 `run_af.py`：起本地 Postgres（积分 schema `points` + 计量抛掷库）、
积分入站（优先 PG）、克隆/复用 kit-meter@`e80920eb`、起 Hub、跑 A–F、写证据。

`meter-hub-joint-debug-af.sh` 是计量侧预备泵的拷贝，C–F 已按同一矩阵补全；
对账报告仍以 Python 跑次为准。

## 夹具账户（仅本机）

| account_id | 积分 | 用于 |
|---|---|---|
| `acct_joint_debug` | 1_000_000 | A/B/D/E |
| `acct_joint_debug_poor` | 1 | C 大额 hold → rejected |
| `acct_joint_debug_short` | 5 | F hold 4pt / settle 8pt → shortfall |
| `acct_demo` | 1_000_000 | 与薄壳文档兼容 |

费率：`RateTable.from_snapshot()` 的 `demo-text-v0`。

## 证据

- `docs/joint-debug-af-evidence.md` — A–F PASS/FAIL + Hub 问题单（请求/回执原文）
- `docs/joint-debug-af/run.json` 与 `docs/joint-debug-af/evidence/*.json`

回调凭据只用 `POINTS_METER_*` / `KIT_METER_POINTS_CALLBACK_*`。客户 `msk_…` 仅 Hub
`/v1alpha/keys` 业务 API；**禁止**当作回调 Bearer。
