# kit-py

MaxStorm 私有 Python monorepo。Phase 1–2 只放可 import 的积木：契约、身份、积分，外加一个限流占位。不发布到 PyPI，不进公开仓。

## 包边界

| 分发包 | import | 职责 | 依赖 |
|---|---|---|---|
| `maxkit-contracts` | `maxkit_contracts` | Person / Org / PointLot / Entry / Hold 的 JSON Schema，以及积分 golden vectors | `jsonschema` |
| `maxkit-identity` | `maxkit_identity` | Person、Org、验证码、Session、外部身份连接器口 | `maxkit-contracts` |
| `maxkit-points` | `maxkit_points` | lot + entry + hold 单一余额引擎 | `maxkit-contracts` |
| `maxkit-guard` | `maxkit_guard` | 进程内固定窗口限流占位 | 无 |

依赖方向是 contracts ← identity、contracts ← points。identity 与 points **互不 import**。积分引擎里的 `owner_id` 是不透明字符串；应用先用 identity 解析出 Person 或 Org，再把 id 交给 points。

`maxkit_identity.IdentityService.require_person` 是应用层 `require_user` 的无框架核心。FastAPI 依赖包装留在各 app，不放进本仓。

## 积分语义

一条账，不设付费桶 / 免费桶。`PointLot.source`（`grant` / `purchase` / `promo` / `adjust`）只说明这笔点数从哪来，不参与余额分列。

- **lot** 是余额真相：`remaining` 是账上还在的点数，`reserved` 是未关闭 hold 占住的部分。
- **entry** 是只追加的审计行：`grant` / `hold` / `settle` / `release` / `expire`。schema 里的 `adjust` 行本阶段引擎不写；正向调整走 `source=adjust` 的 grant。
- **hold** 是预占。`apply_hold` 只增加 `reserved`，不减少 `remaining`。`settle` 才真正扣掉。`release` 把预占还回去。
- 可花余额 `available = spendable - reserved`。未过期 lot 的 spendable 是 `remaining`；已过期 lot 只剩已被 hold 锁住的部分，未预占的过期点数不能再拿去 hold。
- `expire_due` 把已过期且未预占的点数写成 `expire` 行并从 `remaining` 划掉。扫完之后 `available + held == remaining`。
- 分配顺序：`expires_at` 越早越先花，没有过期时间的 lot 最后花，其次 `created_at`、id。
- 同一 idempotency key、同一 payload 重放返回原 lot / hold，不追加 entry。payload 不同则冲突。`settle` / `release` 对已经处于目标状态的 hold 重放是空操作；对另一终态则报错。
- 过期之后：已经 open 的 hold 仍可 `settle`（下单时锁住的点数算数）。`release` 不会把这些点数放回 available。
- 金额不够时 `apply_hold` 整笔失败，不写半笔预占。
- 产品 SKU 价格（例如 story-clip 的 A / B / `dh_std_2m`）留在应用里。引擎只收 `amount`。需要审计时把 sku 放进 `ref`。

## 身份语义

- Person：邮箱可空（只走外部连接器的账号）、`active` / `frozen`、邮箱是否已验证、所属 org id。
- Org：租户。冻结 org 只挡住继续加人；**不**自动冻结积分。应用要停用某 org 的消费，应先看 identity，再决定是否调用 points。
- 验证码只存哈希。明文只在 `issue_code` 的返回值里出现一次，由调用方发送。连续 5 次错误或过期后作废。
- Session 只存 token 哈希。`require_person` 把 token 解析成 Person。Person 被冻结后，旧 session 也会失败。
- 外部登录是连接器口：应用自己核对接证，连接器只抽出稳定 `subject`。本仓把 `(connector, subject)` 连到 Person，再开 session。这里没有微信 / OAuth 实现。

## 与 yangong / story-clip 的差距（2026-10-16）

本环境的 GitHub 令牌只能打开 `lilei0311/kit-py`。`lilei0311/app-story-clip` 与 `lilei0311/app-yangong` 返回 404，没有读到：

- yangong `points.ts` / `points*.test.ts`
- story-clip `app/credits/`、accounts、ratelimit

因此本仓的 schema 和 `packages/contracts/src/maxkit_contracts/vectors/points_golden.json` 是按需求卡和同日 shared-infra 笔记锁的替身，**不是**从 yangong 测试文件抽出来的原文。字段名若与 yangong 源码不一致，以对照源码后的修订为准。向量文件头里的 `provenance` 写了这件事。

同时：

- 没有把 story-clip 的产品常量写进引擎。
- 没有做 SQLAlchemy 适配。存储是 `PointsStore` / `IdentityStore` 加内存实现，避免在看不到 scw 表结构时猜一套 ORM。
- 没有改 story-clip，也没有让它 import 本仓。
- 无支付适配、无真实用户、无密钥。

## 本阶段不做

真支付、`kit-ts`、model-registry / ai-providers、`agent-memory-infra`、`app-maxstorm-site`、Mario 相关代码、公开发布。

## 开发

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -c "import maxkit_points, maxkit_identity"
```

CI（`.github/workflows/ci.yml`）跑同一组命令，并使用已提交的 `uv.lock`。

## 验收对照

- [x] 私有仓可 `uv sync`，CI 跑 pytest + ruff
- [x] `import maxkit_points` / `import maxkit_identity` 可在隔离烟测里通过
- [x] golden vectors 按 lot+entry+hold、单一余额锁语义；与 yangong 源文件的逐条差异见上一节（源文件本次不可读）
- [x] 无密钥、无真实用户数据
