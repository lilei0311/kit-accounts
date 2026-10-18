# maxkit-s1 · `maxkit-s1-l1` 用法（一页）

S1 L1 格式契约检查：英→中旁白交付稿是否「条数对齐、英文原样、中文非空、LOCK 与术语合规」。只读文本，不联网。

## 运行

```bash
uv run maxkit-s1-l1 \
  --contract contract.yaml --source source.txt --deliverable bilingual.txt \
  [--glossary glossary.csv] [--out report.l1.json]
```

- 契约：YAML，或 Markdown 开头的 `---` YAML frontmatter。必填 `contract_id`、`template`（`A` 或 `B`；`C` 暂未实现，报用法错误）。
- 预设 A：源稿每个非空行一单元；交付稿每行 `en<TAB>zh`、`en || zh`，或 `idx<TAB>en<TAB>zh<TAB>lock<TAB>note`。
- 预设 B：交付稿 CSV，表头含 `en`、`zh`（大小写不敏感），可选 `id,lock,note`；源稿可为含 `en` 列的 CSV 或纯行。
- 术语表 CSV：`term_en,term_zh,required`（required 取 yes/no）。
- 报告默认写到交付稿旁的 `<主名>.l1.json`；`--out` 的文件名必须以 `.l1.json` 结尾（否则 L1-07 失败）。stdout 只有一行摘要。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | L1-01..07 全部 PASS |
| 1 | 至少一项 FAIL（详见报告 `checks` / `failed_rows`） |
| 2 | 用法或 IO 错误：缺文件、非 UTF-8、契约/术语表格式错、模板 C、报告目录不存在等；stderr 以 `maxkit-s1-l1: …` 错误行结尾，不写报告；缺参数或未知参数时，该行之前还会先打印一段 usage（`--help` 退出 0，帮助输出到 stdout） |

## 检查项

L1-01 条数 · L1-02 英文原样 · L1-03 中文非空 · L1-04 idx 对齐（缺/多） · L1-05 LOCK（keep_en/skip/glossary） · L1-06 必选术语出现 · L1-07 报告文件名。

## 注意

- **术语表的英文匹配区分大小写**：术语表行 `Skyglass,天玻,yes` 只命中源稿中的 `Skyglass`。例如源稿两行 `Use Skyglass here.` / `Mention skyglass only.`，若中文都不含 `天玻`，则只有第 1 行（idx 1，L1-06）失败；仅含小写 `skyglass` 的那行不会被该术语行命中，因此通过。若另加一行 `skyglass,天玻,yes`，则 idx 2 也会失败。需要不同大小写时在术语表里各写一行。
- 对照样例见 `tests/fixtures/contrast/`（a=预设 A、b=预设 B；`pass.*` 与单点出错的 `fail_*.*` 成对），全部为虚构文本。
- 开发：仓库根 `uv sync && uv run pytest packages/s1 && uv run ruff check .`
