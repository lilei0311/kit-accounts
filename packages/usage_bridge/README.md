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
map: tokens as `(model_id, "tokens", "in"|"out") → yuan_per_token`, other
modalities as `(model_id, unit_kind) → yuan_per_unit`.

`POINTS_PER_YUAN = 100` is an internal cost-equivalent constant, not a
public SKU price. Missing cards refuse platform billing. BYOK
(`billing != "platform"`) is a no-op. Unit kinds billed: `tokens`,
`images`, `clips`, `chars`. `seconds` is not billed.

Tokens use dual-ledger §0.3:
`points = ceil(input_units × rate_in + output_units × rate_out)`
where `rate_* = yuan_per_token × POINTS_PER_YUAN`. One ceil over the sum.
Conservative `max(input, output)` / single-leg / total-only `units` paths
are abolished. Non-token modalities keep a single unit rate.

Settle top-up that still cannot cover the remainder settles the held
amount and writes a formal `usage_shortfall` ledger entry (delta 0, audit
only, not an overdraft).

```bash
uv run python -m maxkit_usage_bridge
```
