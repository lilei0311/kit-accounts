# kit-py

MaxStorm 公开 Python monorepo（`maxkit-*`）。Phase 1–2 放可 import 的积木：契约、身份、积分、限流占位、用量→积分桥，以及 S1 L1 格式检查。本阶段**不**发布到 PyPI；源码在 GitHub 公开仓库。

日后 EC2 薄壳怎么 import 本仓、以及 HTTP 按区块而不是整页挂，见 [docs/app-wiring.md](docs/app-wiring.md)。本仓不起 HTTP 网关，也不实现 UI / block catalog。

## 包边界

| 分发包 | import | 职责 | 依赖 |
|---|---|---|---|
| `maxkit-contracts` | `maxkit_contracts` | Person / Org / PointLot / Entry / Hold 的 JSON Schema，以及积分 golden vectors | `jsonschema` |
| `maxkit-identity` | `maxkit_identity` | Person、Org、验证码、Session、外部身份连接器口 | `maxkit-contracts` |
| `maxkit-points` | `maxkit_points` | 消费轨 lot + 带符号 entry + hold | `maxkit-contracts` |
| `maxkit-usage-bridge` | `maxkit_usage_bridge` | UsageEvent → RateTable → hold/settle/release；内部价表快照 | `maxkit-points` |
| `maxkit-guard` | `maxkit_guard` | 进程内固定窗口限流占位 | 无 |
| `maxkit-s1` | `maxkit_s1` | S1 L1 英→中旁白格式契约检查（预设 A/B） | `pyyaml` |

依赖方向是 contracts ← identity、contracts ← points ← usage-bridge。identity 与 points **互不 import**。`maxkit_s1` 不 import 其他 maxkit 包。本仓 **永不 import kit-model-router**（价表是 STATIC_CARDS 字段的冻结快照，或由 app 注入）。积分引擎里的 `owner_id` 是不透明字符串；应用先用 identity 解析出 Person 或 Org，再把 id 交给 points / usage bridge。

## S1 L1（英→中旁白格式）

`maxkit-s1-l1` 做格式契约检查。预设 A 是纯文本，预设 B 是带 `en`/`zh` 列的 CSV（表头大小写不敏感）。契约是 YAML，或 Markdown 文件开头的 YAML frontmatter（`---`）。可选术语表 CSV 列：`term_en,term_zh,required`。

```bash
uv run maxkit-s1-l1 --contract contract.yaml --source source.txt --deliverable bilingual.txt
```

预设 A 交付稿：每个非空行一条，写成 `en<TAB>zh` 或 `en || zh`。需要 idx、lock、note 时用 `idx<TAB>en<TAB>zh<TAB>lock<TAB>note`（`note` 里可写 `split_of=<idx>`，且契约要设 `allow_split: true`）。源稿的非空行是单元；`#` 开头、整行 `[[...]]`、全大写行默认 LOCK 为 `keep_en`（可用契约 `locks` 覆盖）。通过退出码 0，任一 L1-01..07 失败退出码 1，用法或读写错误退出码 2。报告默认写在交付稿旁边，文件名为 `<交付稿主名>.l1.json`。本 CLI 读 UTF-8 文本和 CSV。

`maxkit_identity.IdentityService.require_person` 是应用层 `require_user` 的无框架核心。FastAPI 依赖包装留在各 app，不放进本仓。

## 积分语义（消费轨）

`maxkit_points` 是 **消费轨** 账本，词表与 yangong / app-story-clip 对齐，结算行为与 story-clip 的 `apply_consume_settle` 对齐。对照笔记是 2026-10-18 的 `kit-py-golden-gap-2026-10-18`：lot 用原始 `amount` + 可花 `opened`，hold 立刻减少 `opened`；yangong `applySettle` 把冻结额发给 winner 的做法属于 **竞赛轨**，本引擎不实现。

一条账。`origin` 与 `source` 写同一个来源（`signup_grant` / `subscription_grant` / `daily_claim` / `earned` / `admin_adjustment`），不拆成付费桶和免费桶。`paid_only` 只是 B 档过滤：只花 `PAID_TIER_ORIGINS`（`subscription_grant`、`admin_adjustment`、`earned`，与 story-clip 一致）。`daily_claim` 和 `signup_grant` 仍在同一条账上，但是赠送/体验，不进这个过滤。不是产品 SKU 价目。

| | 消费轨（本仓） | 竞赛轨（不实现，留给 kit-ts / 另卡） |
|---|---|---|
| lot | `amount` 保持发放原值，`opened` 是还能花的部分 | yangong 把余额直接记在 `amount` 上 |
| hold | 立刻减少 `opened`，写负的 `task_hold` | 立刻减少 `amount`，写负的 `task_hold` |
| settle | `apply_consume_settle`：hold → `settled`，每片写 `delta = 0` 的 `task_settlement`。不退回 `opened`，不给别人入账 | `applySettle`：给 winner 开一笔 `earned` |
| release | 未过期的 slice 把 `opened` 加回，写 `task_release`。已过期的 slice 写 `delta = 0` 的 `expiry`，不把冻结续命 | 同左（未过期加回，过期零额） |
| hold 状态 | `held` / `released` / `settled` | 同左 |

- 分录带符号（与 kit-meter points-contract 对齐）：`signup_grant`、`subscription_grant`、`daily_claim`、`admin_adjustment` 以及 `origin=earned` 的发放（记成正的 `task_settlement`）会开 lot。`task_hold` 为负；`task_release` 为正（回冲）；消费结算的 `task_settlement` 是零额审计（多退少补已由补 hold / release 落到余额）；正式 kind `usage_shortfall` 也是零额审计，**不作透支**。扫掉过期可花余额的 `expiry` 为负。
- `available` 只加总还没过期且 `opened > 0` 的 lot。`held_amount` 是状态仍为 `held` 的冻结合计。冻结已经从 `opened` 里划走。
- `apply_expiry` 把已到期的 `opened` 划成 0。hold 在扣款前会先扫本 owner 的过期余额；钱不够整笔失败，但这次扫除会留下。
- 分配顺序（FEFO）：`expires_at` 越早越先花，没有过期时间的 lot 最后花，其次 `granted_at`、id。
- 幂等键是 grant 的 `key`（落在 lot.`grant_key` 上）和 hold 的 `key`。同一 key、同一 payload 重放返回原 lot / hold，不追加分录。payload 不同则冲突。已经 `settled` 或 `released` 的 hold 再做同一动作是空操作；对另一终态则报错。
- 余额可以由分录重建：`rebuild_lots` / `PointsEngine.balances_match_ledger`。零额审计不改 `opened`。正的 `task_settlement` 会重建成 `earned` lot，那是 `apply_grant(origin="earned")` 的形状；消费结算不写这种正数。
- 同一 owner 可以同时有多笔 `held`。幂等按 hold key，不按任务 id 去重。
- 产品 SKU 价格留在应用里。引擎只收点数 `amount`。需要审计时把外部 id 放进 `ref`。
- 存储是内存里的 `PointsStore`。SQLAlchemy 不在本阶段。
- 稳定调用面（供日后 FastAPI 薄壳直接 import，本仓不起 HTTP）：`apply_hold`（幂等 `key`）、`apply_consume_settle`、`apply_release`。没有 `settle` 别名，以免和竞赛轨 `applySettle` 混淆。

## 用量 → 积分桥

`maxkit_usage_bridge` 把应用层的 `UsageEvent` 换成消费轨预扣。`POINTS_PER_YUAN = 100` 是内部成本当量常数，不是对外 SKU 价。价表字段名来自 kit-model-router `STATIC_CARDS` 的冻结拷贝（`price_per_million_*` / `price_per_image_yuan` / `price_per_clip_yuan` / `price_per_1k_chars_yuan`），不是 live import。

- 计费 unit：`tokens` / `images` / `clips` / `chars`。`seconds` 不计。`billing=platform` 才扣；BYOK 默认跳过。无价格卡则拒绝平台计费。
- tokens：RateTable 提供 **in/out 两键** `(model_id, "tokens", "in"|"out")` → yuan/token。`points = ceil(input_units × rate_in + output_units × rate_out)`，其中 `rate_* = yuan_per_token × 100`。整笔一次 ceil，禁止 `max(in,out)` / 单腿 / 仅合计 `units`。
- 非 tokens：键仍是 `(model_id, unit_kind)` → `yuan_per_unit`；`points = ceil(units × yuan_per_unit × 100)`，有用量最少 1 分。
- Sink：调用前 `hold`（`idempotency_key=request_id`），成功 `settle` 到实际（多退少补），失败 `release`。补 hold 仍不足时 settle 已 hold 部分，并写正式 `usage_shortfall` 审计分录（delta 0，不作透支）。
- 应用注入 Sink，并传入 `account_id` / `request_id`。库本身不是计费网关。接线说明见 [docs/app-wiring.md](docs/app-wiring.md)。

## 身份语义

- Person：邮箱可空（只走外部连接器的账号）、`active` / `frozen`、邮箱是否已验证、所属 org id。
- Org：租户。冻结 org 只挡住继续加人；**不**自动冻结积分。应用要停用某 org 的消费，应先看 identity，再决定是否调用 points。
- 验证码只存哈希。明文只在 `issue_code` 的返回值里出现一次，由调用方发送。连续 5 次错误或过期后作废。
- Session 只存 token 哈希。`require_person` 把 token 解析成 Person。Person 被冻结后，旧 session 也会失败。
- 外部登录是连接器口：应用自己核对接证，连接器只抽出稳定 `subject`。本仓把 `(connector, subject)` 连到 Person，再开 session。这里没有微信 / OAuth 实现。

## 与 yangong / story-clip 的边界

消费轨语义来自 yangong `points.ts` / `points.test.ts` 的发放、冻结、释放、过期、FEFO、幂等、余额不足，加上 app-story-clip `app/credits` 的消费结算（`apply_consume_settle`：终态 + 零额审计）。`packages/contracts/src/maxkit_contracts/vectors/points_golden.json` 的 `provenance`、`excluded` 和每条 `from` 写了对应关系。`kit-py-golden-gap-2026-10-18` 是这次修订的对照笔记。

`earned` 是 lot 的 `origin`，不是单独的 entry kind。发放 `origin=earned` 时，分录 kind 按 yangong `applyGrant` 写成正的 `task_settlement`。消费结算复用同一种 kind，但 `delta` 为 0，不会开新 lot。

本环境的 GitHub 凭证对 `lilei0311/app-story-clip` 返回 404，读不到 `app/credits` 源文件。消费结算的形状按对照笔记和点名的 `apply_consume_settle` 锁死：hold 改为 `settled`，每片一条零额 `task_settlement`，不退回 `opened`，不给 winner 入账。`PAID_TIER_ORIGINS` 是 `subscription_grant`、`admin_adjustment`、`earned`，不含 `daily_claim`。

仍留在仓外：

- 竞赛轨 winner 入账（yangong `applySettle`）。
- 订阅周期、每日领取的产品规则，以及 story-clip 的 SKU 价格。
- SQLAlchemy / 真 ORM。应用自己实现 `PointsStore` 之前，用内存适配。
- 不改 story-clip，也不让它 import 本仓。
- 无支付适配、无真实用户、无密钥。
- 不建 `app-maxstorm-api`、不上 EC2、不起 HTTP 网关。组件式布局（`type` + `props` + `slots`/`children`）只在接线文档里钉方向，本仓不实现 block catalog / React / Vue。

## 本阶段不做

真支付、`kit-ts`、`agent-memory-infra`、`app-maxstorm-site`、Mario 相关代码。仓库已公开；本阶段仍不发布到 PyPI。`model-registry` / `ai-providers` 不进本 monorepo：MaxStorm 模型路由在独立基础设施里，本仓不实现。

## 开发

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -c "import maxkit_points, maxkit_identity, maxkit_s1, maxkit_usage_bridge"
uv run python -m maxkit_usage_bridge
uv run maxkit-s1-l1 --contract contract.yaml --source source.txt --deliverable bilingual.txt
```

CI（`.github/workflows/ci.yml`）跑同一组命令，并使用已提交的 `uv.lock`。

## 验收对照

- [x] 公有仓可 `uv sync`，CI 跑 pytest + ruff
- [x] `import maxkit_points` / `import maxkit_identity` / `import maxkit_usage_bridge` 可在隔离烟测里通过
- [x] golden vectors 锁消费轨（opened、带符号分录、零额 consume settle）；竞赛轨用例列在向量文件的 `excluded` 里，不执行
- [x] 无密钥、无真实用户数据
