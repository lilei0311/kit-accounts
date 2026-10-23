# maxkit-usage-bridge

Application-layer bridge from a `UsageEvent` to the consume-track points
ledger. `kit-model-router` only emits usage. This package converts units to
points and calls `PointsEngine.apply_hold` / `apply_consume_settle` /
`apply_release`. It is not a billing gateway: the app injects the sink and
passes `account_id` / `request_id`.

**This package must never import `kit-model-router`.** The rate snapshot
copies STATIC_CARDS field names (`price_per_million_input`,
`price_per_million_output`, `price_per_image_yuan`, `price_per_clip_yuan`,
`price_per_1k_chars_yuan`) into a frozen table. Apps may inject their own
`(model_id, unit_kind) → yuan_per_unit` map instead.

`POINTS_PER_YUAN = 100` is an internal cost-equivalent constant, not a
public SKU price. Missing cards refuse platform billing. BYOK
(`billing != "platform"`) is a no-op. Unit kinds billed: `tokens`,
`images`, `clips`, `chars`. `seconds` is not billed.

Tokens v0 use a single `units` field and a conservative
`max(input, output) / 1e6` yuan-per-token. Input/output split stays in
another lane.

```bash
uv run python -m maxkit_usage_bridge
```
