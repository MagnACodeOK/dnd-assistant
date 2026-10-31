from dnd_assistant.glossary import build_prompt, collect_names


def test_collect_names_order_and_dedupe(tmp_path):
    (tmp_path / "glossary.md").write_text("# header\nIreena\n\n", encoding="utf-8")
    (tmp_path / "NPCs").mkdir()
    (tmp_path / "NPCs" / "Ireena Kolyana.md").write_text("", encoding="utf-8")
    (tmp_path / "Places").mkdir()
    (tmp_path / "Places" / "Krezk.md").write_text("", encoding="utf-8")
    assert collect_names(tmp_path, ["Paylon", "ireena"]) == ["Paylon", "ireena", "Ireena Kolyana", "Krezk"]


def test_collect_names_missing_files(tmp_path):
    assert collect_names(tmp_path) == []
    assert collect_names(tmp_path, ["Paylon"]) == ["Paylon"]


def test_build_prompt_format():
    assert build_prompt(["Ireena", "Strahd"]) == "Curse of Strahd D&D session. Names: Ireena, Strahd."


def test_build_prompt_truncates_at_name_boundary():
    names = [f"Name{i:03d}" for i in range(200)]
    prompt = build_prompt(names, max_chars=100)
    assert len(prompt) <= 100
    assert prompt.endswith(".")
    listed = prompt.removeprefix("Curse of Strahd D&D session. Names: ").removesuffix(".").split(", ")
    assert listed == names[: len(listed)]
    assert len(listed) > 1


def test_build_prompt_no_names():
    assert build_prompt([]) == "Curse of Strahd D&D session."
