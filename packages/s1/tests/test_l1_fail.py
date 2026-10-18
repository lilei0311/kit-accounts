"""Each L1 assertion can fail on its own fictional case."""

from datetime import UTC, datetime

from maxkit_s1 import run_l1

NOW = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)


def _run(case):
    return run_l1(
        case / "contract.yaml",
        case / "source.txt",
        case / "deliverable.txt",
        glossary_path=case / "glossary.csv",
        now=NOW,
    )


def test_l1_01_and_l1_04_when_a_line_is_dropped(pass_a, schema, failed):
    path = pass_a / "deliverable.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    outcome = _run(pass_a)
    assert outcome.exit_code == 1
    schema(outcome.report)
    failed(outcome.report, {"L1-01", "L1-04"})
    assert any(
        row["idx"] == 7 and row["detail"] == "missing unit" for row in outcome.report["failed_rows"]
    )


def test_l1_02_en_not_preserved(pass_a, failed):
    path = pass_a / "deliverable.txt"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "The copper fox crossed the quiet ridge.",
        "The copper fox left the quiet ridge.",
        1,
    )
    path.write_text(text, encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-02"})
    assert outcome.report["failed_rows"] == [{"idx": 1, "id": "L1-02", "detail": "en mismatch"}]


def test_l1_03_empty_zh(pass_a, failed):
    path = pass_a / "deliverable.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    en, _zh = lines[0].split("\t", 1)
    lines[0] = en + "\t"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-03"})
    assert outcome.report["failed_rows"][0]["detail"] == "empty zh"


def test_l1_04_same_count_missing_and_extra_idx(tmp_path, failed):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-idx\ntemplate: A\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text(
        "Alpha one stays.\nBeta two stays.\nGamma three stays.\n",
        encoding="utf-8",
    )
    deliverable.write_text(
        "\n".join(
            [
                "1\tAlpha one stays.\t甲一\tnone\t",
                "2\tBeta two stays.\t乙二\tnone\t",
                "4\tGamma three stays.\t丙四\tnone\t",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.report["counts"]["source_units"] == 3
    assert outcome.report["counts"]["deliverable_units"] == 3
    failed(outcome.report, {"L1-04"})
    details = {(row["idx"], row["detail"]) for row in outcome.report["failed_rows"]}
    assert details == {(3, "missing unit"), (4, "extra unit")}


def test_l1_05_keep_en_paraphrase(pass_a, failed):
    path = pass_a / "deliverable.txt"
    text = path.read_text(encoding="utf-8").replace(
        "HOLD FRAME\tHOLD FRAME", "HOLD FRAME\t保持画面"
    )
    path.write_text(text, encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-05"})
    assert outcome.report["failed_rows"][0]["idx"] == 6
    assert "keep_en" in outcome.report["failed_rows"][0]["detail"]


def test_l1_05_skip_must_not_be_translated(pass_a, failed):
    path = pass_a / "deliverable.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = "[[STAGE]]\t舞台"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-05"})
    assert outcome.report["failed_rows"][0]["detail"] == "skip: zh must be empty or copy en"


def test_l1_05_glossary_lock_and_l1_06(pass_a, failed):
    path = pass_a / "deliverable.txt"
    text = path.read_text(encoding="utf-8").replace(
        "这一行使用术语北风协议。", "这一行没有写入约定译法。"
    )
    path.write_text(text, encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-05", "L1-06"})
    assert any(row["id"] == "L1-05" and row["idx"] == 5 for row in outcome.report["failed_rows"])
    assert any(row["id"] == "L1-06" and row["idx"] == 5 for row in outcome.report["failed_rows"])


def test_l1_06_required_term_on_unlocked_line(pass_a, failed):
    path = pass_a / "deliverable.txt"
    text = path.read_text(encoding="utf-8").replace("它提着一盏玻璃灯。", "它提着一盏灯。")
    path.write_text(text, encoding="utf-8")
    outcome = _run(pass_a)
    failed(outcome.report, {"L1-06"})
    assert outcome.report["failed_rows"] == [
        {"idx": 2, "id": "L1-06", "detail": "missing required term '玻璃灯'"}
    ]


def test_l1_07_report_name(pass_a, failed, by_id):
    out = pass_a / "notes.json"
    outcome = run_l1(
        pass_a / "contract.yaml",
        pass_a / "source.txt",
        pass_a / "deliverable.txt",
        glossary_path=pass_a / "glossary.csv",
        out_path=out,
        now=NOW,
    )
    assert outcome.exit_code == 1
    assert out.is_file()
    failed(outcome.report, {"L1-07"})
    assert by_id(outcome.report)["L1-07"]["detail"] == "report name must end with .l1.json"


def test_glossary_match_is_case_sensitive(tmp_path, failed):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    glossary = tmp_path / "glossary.csv"
    contract.write_text(
        "contract_id: S1-FC-20261018-case\ntemplate: A\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text("Use Skyglass here.\nMention skyglass only.\n", encoding="utf-8")
    deliverable.write_text(
        "Use Skyglass here.\t这里用 skyglass。\nMention skyglass only.\t只提到 skyglass。\n",
        encoding="utf-8",
    )
    glossary.write_text("term_en,term_zh,required\nSkyglass,天玻,yes\n", encoding="utf-8")
    outcome = run_l1(contract, source, deliverable, glossary_path=glossary, now=NOW)
    failed(outcome.report, {"L1-06"})
    assert outcome.report["failed_rows"] == [
        {"idx": 1, "id": "L1-06", "detail": "missing required term '天玻'"}
    ]


def test_undeclared_skip_on_deliverable_fails_lock(tmp_path):
    contract = tmp_path / "contract.yaml"
    source = tmp_path / "source.txt"
    deliverable = tmp_path / "deliverable.txt"
    contract.write_text(
        "contract_id: S1-FC-20261018-lock\ntemplate: A\nauto_lock: false\n",
        encoding="utf-8",
    )
    source.write_text("Alpha one stays.\n", encoding="utf-8")
    deliverable.write_text("1\tAlpha one stays.\t\tskip\t\n", encoding="utf-8")
    outcome = run_l1(contract, source, deliverable, now=NOW)
    assert outcome.exit_code == 1
    assert any("lock mismatch" in row["detail"] for row in outcome.report["failed_rows"])
