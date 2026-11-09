# Hub→积分回调入站（联调冻结契约 · 本地适配 + HTTP 薄壳）

本页描述 **kit-accounts 积分账** 对计量 Hub 回调信封的入站适配，以及本地/预发 HTTP 薄壳。依据是 2026-11-09 **联调冻结版** 契约（账户侧可写码；Hub 可靠投递未验收）。

**这不是真投递，也不是合闸声明。**

- Hub 钩子仍空：无 outbox、无可靠投递验收。账户侧现可在本地收 `POST /internal/v0/meter-events`，供与 Hub 本地推送对打。
- parking / 人工同 `event_id` 重放是运维/Hub 侧，本刀不做。
- 双账隔离：适配器只写积分账（`PointsEngine` / `PointsUsageSink`）。**不** import、不写 kit-meter、kit-model-router、成本账。
- **不**接真实扣费、**不**部署生产、**不**把客户 `msk_…` 或 `KIT_METER_API_TOKEN` 当回调凭据。

库入口：`maxkit_usage_bridge.MeterEventAdapter.handle(envelope) -> MeterCallbackAck`。
HTTP 薄壳在 `maxkit_internal_http`：鉴权后调用 Adapter；**本地事务提交后再 HTTP 200**。

## 本地/预发起服

默认监听 **`http://127.0.0.1:8090`**，路径仅 `POST /internal/v0/meter-events`。

| 环境变量 | 作用 |
|---|---|
| `POINTS_METER_CALLBACK_TOKEN` | `Authorization: Bearer …` 联调服务凭据（必填） |
| `POINTS_METER_CALLBACK_CREDENTIAL_ID` | 请求头 `credential_id`（必填） |
| `POINTS_METER_HTTP_HOST` | 绑定地址，默认 `127.0.0.1` |
| `POINTS_METER_HTTP_PORT` | 端口，默认 `8090` |

**禁止**用客户 `msk_…` 密钥，也 **禁止**用 kit-meter 的 `KIT_METER_API_TOKEN`。进程只读上表 `POINTS_METER_*` 变量；以 `msk_` 开头的值会拒绝启动。

联调假凭据示例（**仅联调**，可写死在本地文档/dotenv，不是生产密钥）：

```
POINTS_METER_CALLBACK_TOKEN=local-only-meter-callback
POINTS_METER_CALLBACK_CREDENTIAL_ID=cred_local_points_meter
```

```bash
export POINTS_METER_CALLBACK_TOKEN=local-only-meter-callback
export POINTS_METER_CALLBACK_CREDENTIAL_ID=cred_local_points_meter
uv run python -m maxkit_internal_http
# 或：uv run maxkit-meter-http
# listening on http://127.0.0.1:8090/internal/v0/meter-events
```

本地进程用内存账本，并给 `acct_demo` 发放演示积分；价表是 `RateTable.from_snapshot()`（hold 请用 `demo-text-v0`，契约草稿里的 `demo-model` 没有费率卡）。

一条 hold（成功则 HTTP 200、`result=applied`、带 `snapshot_ref`）：

```bash
curl -sS -X POST 'http://127.0.0.1:8090/internal/v0/meter-events' \
  -H 'Authorization: Bearer local-only-meter-callback' \
  -H 'credential_id: cred_local_points_meter' \
  -H 'Content-Type: application/json' \
  -d '{
    "schema_version":"0",
    "event_id":"evt_hold_550e8400-e29b-41d4-a716-446655440000",
    "kind":"task_hold",
    "occurred_at":"2026-11-09T13:00:00Z",
    "request_id":"550e8400-e29b-41d4-a716-446655440000",
    "account_id":"acct_demo",
    "billing":"platform",
    "estimate":{"model_id":"demo-text-v0","modality":"text","unit_kind":"tokens","input_units":"100","output_units":"20"}
  }'
```

鉴权失败 → 401 `service_auth_invalid` / 403 `service_forbidden`。缺头或错 Bearer 为 401；凭据 id 不匹配为 403。

## 信封（schema_version `"0"`）

`kind`：`task_hold` / `task_settlement` / `task_release`。

| 字段 | 角色 |
|---|---|
| `request_id` | **财务主键**（全局唯一）。同标识同种记账只记一次。 |
| `kind` | 与 `request_id` 组成业务去重键。 |
| `event_id` | **运输身份**，确定性 `evt_hold_<request_id>` / `evt_settle_<request_id>` / `evt_release_<request_id>`。不是账本主键，不写入 entry.`ref`。 |
| `occurred_at` | 原事件时间；重试保持不变。**不参与**财务比较。 |
| `account_id` / `billing` | 账户绑定不可变。`platform` 才落积分；`byok` 跳过（Hub 按契约不应发这三类回调，入站仍按现有 skip 语义）。 |
| `estimate` / `meter` | 原始用量，**不带积分数**。费率与 snapshot 在账户侧。 |
| `snapshot_ref` | hold 成功回显的不透明引用。settle/release **可省略**（按 `request_id` 查本地绑定）；显式错引用 → `payload_conflict`。 |
| `reason` | release 可选；超时例：`hold_timeout`。账户不自超时。 |

数量一律 **十进制字符串**，非负、有限。`unit_kind=tokens` 必须同时有 `input_units` 与 `output_units`，禁止 `units`。非 tokens 必须有 `units`，禁止夹带 input/output。`meter.estimated` 原样保留。

## 提交后业务码（本地 ACK → HTTP）

账户须在本地事务提交后再返回判定。薄壳把 Adapter 的 `http_status` 写到 HTTP 状态行，JSON 回显 `schema_version` / `event_id` / `request_id` / `kind`，以及 `result` / `code` 等。

| HTTP | result / code | 含义 |
|---|---|---|
| 200 | `applied` | 新记账成功。hold 成功含 `snapshot_ref`。 |
| 200 | `duplicate` | 同业务结果已在；含 `original_result` 与可用 `snapshot_ref`。 |
| 200 | `rejected` | 已持久拒绝（如余额不足）。≠ 预占成功。 |
| 200 | `shortfall` | settle 已按持有额落账并写 `usage_shortfall`；**停止重试**，本期不自动追扣。 |
| 200 | `applied` + `effect=cancelled_without_hold` | 无 hold 的 release 落取消墓碑，**不退款**；迟到 hold → `rejected`，不扣款。 |
| 200 | `skipped` | 防御性 BYOK/非 platform：与现有 sink skip 一致，不借记。Hub 不应发送。 |
| 409 | `predecessor_pending` | 暂时缺前序 hold（乱序），**可重试**。不持久化该 settle。 |
| 409 | `payload_conflict` / `terminal_conflict` | 财务字段打架或终态互斥（含 rejected 后 settle/release）。**不可重试**。 |
| 422 | `rate_config_missing` | 缺费率，配置挂起（`pending_config`），不是永久业务拒绝。 |
| 400 | `invalid_payload` / `unsupported_schema` | 校验失败；不得改同一 `event_id` 的财务载荷重放。 |
| 401 / 403 | `service_auth_invalid` / `service_forbidden` | 回调凭据错误；修配置后重放，不弃消息。 |

乱序缺 hold 的 settle → `predecessor_pending`。非法（已 rejected / 已取消墓碑 / 已终态）→ `terminal_conflict`。二者必须区分。

## 明确不做

- 生产部署、NAS/生产迁移、真实计费流量、把真实密钥写入仓库
- Hub 仓改动、outbox、CAS 终态选择、parking 管理 API
- 把空钩子、本适配器绿测、或本地 HTTP 200 当成「生产已到账」
