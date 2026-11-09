# 双账真对打证据（冻结契约 A–F）

- 时间（UTC）：`2026-11-09T23:51:30Z`
- kit-accounts HEAD：`c3737cfb5834b43bc0318f37fcc598563aad9232`
- kit-meter pin：`40967aae`
- kit-meter HEAD（完整 SHA）：`UNAVAILABLE`
- kit-meter 树：`UNAVAILABLE`
- 积分存储：`postgres`
- clone：git clone failed: Cloning into '/tmp/dual-ledger/kit-meter'...
remote: Repository not found.
fatal: repository 'https://github.com/lilei0311/kit-meter.git/' not found

范围：本机 127.0.0.1；假回调凭据 `local-only-meter-callback`；
不部署、不接真扣费、不用客户 `msk_…` 当回调、不改两仓业务代码、不在 kit-meter 开 PR。

## 环境

- points ledger: local Postgres kit_accounts_joint (schema points)
- git clone failed: Cloning into '/tmp/dual-ledger/kit-meter'...
remote: Repository not found.
fatal: repository 'https://github.com/lilei0311/kit-meter.git/' not found
- points smoke hold HTTP 200 body={'schema_version': '0', 'event_id': 'evt_hold_550e8400-e29b-41d4-a716-446655440000', 'request_id': '550e8400-e29b-41d4-a716-446655440000', 'kind': 'task_hold', 'result': 'applied', 'snapshot_ref': 'e8be3030414cddc22848cc01134428ce7f1a99d72a69c403a199d1b4132e0a89'}
- points smoke ledger billing={'request_id': '550e8400-e29b-41d4-a716-446655440000', 'account_id': 'acct_joint_debug', 'status': 'held', 'snapshot_ref': 'e8be3030414cddc22848cc01134428ce7f1a99d72a69c403a199d1b4132e0a89', 'points': 1, 'shortfall_points': 0, 'reject_reason': None, 'available_after': 999999, 'effect': {'points': 1, 'outcome': 'held', 'hold_ids': ['hold_00000001'], 'available': 999999, 'operation': 'hold'}, 'skipped_reason': None, 'billing': 'platform', 'model_id': 'demo-text-v0', 'unit_kind': 'tokens'} available=999999 held=1

## A–F 一句话

- **A FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **B FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **C FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **D FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **E1 FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **E2 FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。
- **F FAIL**（卡点：环境） `-` — kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

## 逐条对账

### A FAIL — 正常 hold→start→freeze/settle

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### B FAIL — 超时 hold→expire-holds/release

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### C FAIL — 余额不足 rejected + 禁 start

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### D FAIL — 丢 ACK duplicate

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### E1 FAIL — Hub 无 hold 先 release 墓碑；再 hold 须 409

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### E2 FAIL — 本地模拟在途 task_hold（非 Hub 端到端）

- request_id: `n/a`
- 模式: `points-inbound-sim`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

### F FAIL — settle shortfall 停重试

- request_id: `n/a`
- 模式: `hub`
- 卡点归属: **环境**
- 摘要: kit-meter 树不可用（需 KIT_METER_REPO_ROOT 指向固定 SHA，或可克隆且 HEAD 匹配 KIT_METER_PIN=40967aae）。本云不反复重试私仓 clone，不声称联合验收已过。

Hub `meter.tasks`：

```json
null
```

Hub `points_outbox`：

```json
null
```

积分 HTTP 回执：

```json
[]
```

积分 `billing_requests` + entries + 余额：

```json
{
  "billing": null,
  "entries": [],
  "balance": null
}
```

## Hub 侧问题（现象 + 请求/回执原文）

### env: kit-meter tree unavailable or pin mismatch

git clone failed: Cloning into '/tmp/dual-ledger/kit-meter'...
remote: Repository not found.
fatal: repository 'https://github.com/lilei0311/kit-meter.git/' not found

请求：

```json
{
  "url": "https://github.com/lilei0311/kit-meter.git",
  "pin": "40967aae",
  "default_pin": "40967aae"
}
```

回执：

```json
{
  "head": null,
  "root": null
}
```

## 建议（仅报告，未改业务代码）

- 若 kit-meter 树不可用：把完整固定 SHA 放到 `KIT_METER_PIN`
  （默认锁 `40967aae` = kit-meter PR #9，不要合），
  `KIT_METER_REPO_ROOT` 的 HEAD 必须与 pin 一致；不一致会报错，
  不会悄悄 checkout。仅当明确 `KIT_METER_CHECKOUT_PIN=1` 才拨 HEAD。
- C：Hub 必须把积分 `result=rejected`（HTTP 200）当成 hold 失败，
  禁止 start/freeze，且不得再发 release 退款。
- D：丢 ACK / 同 `event_id` 重放应回 `duplicate` + `original_result=applied`
  + 原 `snapshot_ref`；Hub 仅在任务仍有效时放行模型。
- E1：Hub 真实无 hold 先 release → 积分 `cancelled_without_hold`、entries=[]、
  outbox acked；终态后再 hold 必须 Hub HTTP 409（不可用积分 rejected 凑绿）。
- E2：本地直发积分 `/internal/v0/meter-events` 模拟在途 `evt_hold_<rid>`，
  须 `result=rejected` 且无新扣款。非 Hub 端到端；E1 的 409 不能替代 E2。
- F：`shortfall` 已落账，Hub 应将 outbox 标终态并停止重试。

