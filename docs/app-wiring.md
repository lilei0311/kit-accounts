# App / EC2 接线（本仓只留调用面）

本页给日后 EC2 薄壳（FastAPI 等）看。**本 PR 不建 API 仓、不部署 EC2、不起 HTTP 网关、不实现真实 UI。**

两库 **互不 import**：`kit-accounts` 永不 import `kit-model-router`；`kit-model-router` 永不 import `kit-accounts`。网关 / HTTP 面只在未来 app 仓，不进 kit。RateTable 是内部成本当量，不是对外 SKU 价。

## 日后薄壳怎么挂（伪代码）

```
# future app-maxstorm-api (NOT in this repo)
from maxkit_identity import IdentityService
from maxkit_points import PointsEngine, MemoryPointsStore
from maxkit_usage_bridge import (
    POINTS_PER_YUAN,   # 100; config constant, not a public price
    PointsUsageSink,
    RateTable,
    UsageEvent,
)

engine = PointsEngine(store=app_points_store)          # app implements PointsStore
rates = RateTable.from_snapshot()                      # or inject app-owned cards
# rates = RateTable(rates={(model_id, unit_kind): yuan_per_unit})
sink = PointsUsageSink(engine, rates)                  # app injects the sink

person = identity.require_person(session_token)
event = UsageEvent(model_id=..., modality="text", units=estimated, unit_kind="tokens")

# 1) hold before the vendor call — idempotency_key = request_id
held = sink.hold(event, account_id=person.id, request_id=request_id, billing="platform")
# billing != "platform" (BYOK) → no-op; missing rate card → refuse; insufficient → raise

# 2) kit-model-router (imported only by the app, never by this kit) emits UsageEvent
# 3) success → settle to actual units (over-hold release Δ / under-hold top-up then settle)
#    failure → sink.release(account_id=person.id, request_id=request_id)

# Direct ledger verbs stay stable if the app already has a point amount:
#   engine.apply_hold(owner_id=..., amount=..., key=request_id)
#   engine.apply_consume_settle(hold_id)
#   engine.apply_release(hold_id)
# There is no PointsEngine.settle alias (contest-track applySettle is a different verb).
```

`UsageEvent` 本身只有 `model_id` / `modality` / `units` / `unit_kind`。`account_id` 与 `request_id` 由应用上下文传入 Sink。tokens v0 仍是单 `units` 字段，价表用 `max(input, output) / 1e6` 保守折算；进/出拆分不在本仓。

## 组件式布局（日后 HTTP 形态 · 本 PR 只钉方向）

日后 EC2 HTTP **按可嵌入区块挂，不按整页 HTML/SSR 挂**。真实 block 目录 / 区块 API 等 EC2 组装刀再落地；本仓 kit **保持纯领域/数据 API**（points、RateTable、usage bridge），**不**放 React/Vue，也 **不** 在本 PR 实现 block catalog。

| 原则 | 说明 |
|---|---|
| 区块 | 余额条、用量明细、模型选择卡、任务状态卡等可组合 blocks；前端按需嵌入 |
| 数据契约 | JSON / typed blocks：`type` + `props` + 可选 `slots` / `children`；不要一次返回整页 DOM |
| 布局 | 轻量 layout tree（slot / region → block ids）；前端自己渲染皮肤 |
| 本轮不做 | 后端 SSR 整站、把组件源码塞进 kit、实现真实 UI |

方向示意（**不是**本仓 API，仅文档）：

```
{
  "type": "layout",
  "props": {"region": "account-rail"},
  "slots": {
    "header": {"type": "balance_bar", "props": {"account_id": "...", "available": 996}},
    "body": {
      "type": "stack",
      "children": [
        {"type": "usage_detail", "props": {"request_id": "...", "points": 4}},
        {"type": "model_picker_card", "props": {"model_id": "demo-text-v0"}},
        {"type": "task_status", "props": {"status": "settled"}}
      ]
    }
  }
}
```

kit 继续返回数据（`Balance`、`BillingResult`、`UsageEvent`）。薄壳以后把这些数据填进 block `props`；皮肤归前端。
