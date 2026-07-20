import re
from pathlib import Path


def test_formatted_ddl_has_no_unescaped_empty_json_object_literal():
    source = (Path(__file__).parents[1] / "app" / "db.py").read_text()
    formatted_sql_blocks = re.findall(
        r"sql\.SQL\(\s*\"\"\"(.*?)\"\"\"\s*\)\.format",
        source,
        flags=re.DOTALL,
    )
    assert formatted_sql_blocks
    for block in formatted_sql_blocks:
        assert "DEFAULT '{}'::jsonb" not in block

    # Five JSON object defaults are expected in CREATE TABLE templates.
    assert sum(block.count("DEFAULT '{{}}'::jsonb") for block in formatted_sql_blocks) == 5
