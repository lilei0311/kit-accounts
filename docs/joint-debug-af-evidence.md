# 双账真对打证据（冻结契约 A–F）

- 时间（UTC）：`2026-11-09T23:09:34Z`
- kit-accounts HEAD：`963c43197a6ed02ff11e9bc7a5589bec22ba4afc`
- kit-meter pin：`e80920eb`
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

- **A FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。
- **B FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。
- **C FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。
- **D FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。
- **E FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。
- **F FAIL**（卡点：环境） `-` — 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

## 逐条对账

### A FAIL — 正常 hold→start→freeze/settle

- request_id: `n/a`
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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

### E FAIL — 无 hold 先 release 墓碑

- request_id: `n/a`
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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
- 卡点归属: **环境**
- 摘要: 无法克隆 kit-meter（GitHub App 仅授权 kit-accounts，私仓 404）。把 kit-meter 加进 Cursor GitHub App 或设置 KIT_METER_REPO_ROOT 后重跑。

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

### env: kit-meter clone denied

git clone failed: Cloning into '/tmp/dual-ledger/kit-meter'...
remote: Repository not found.
fatal: repository 'https://github.com/lilei0311/kit-meter.git/' not found

请求：

```json
{
  "url": "https://github.com/lilei0311/kit-meter.git",
  "pin": "e80920eb"
}
```

回执：

```json
{
  "repository_selection": "selected",
  "visible": [
    "kit-accounts"
  ]
}
```

## 建议（仅报告，未改业务代码）

- 若 clone 失败：把 `lilei0311/kit-meter` 加入 GitHub App selected repos，
  或把已 checkout `e80920eb` 的树放到 `KIT_METER_REPO_ROOT`。
- C：Hub 必须把积分 `result=rejected`（HTTP 200）当成 hold 失败，
  禁止 start/freeze，且不得再发 release 退款。
- D：丢 ACK / 同 `event_id` 重放应回 `duplicate` + `original_result=applied`
  + 原 `snapshot_ref`；Hub 仅在任务仍有效时放行模型。
- E：Hub 在从未 applied hold 时仍须能投递 `task_release`（墓碑）；
  账户已实现 `cancelled_without_hold`。
- F：`shortfall` 已落账，Hub 应将 outbox 标终态并停止重试。

