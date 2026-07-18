"""自定义提示词模板（issue #581）。

每个故事模式一行：mode → 用户编辑过的模板全文（含 {words} 等记号）。
没有行 = 使用 ai.DEFAULT_PROMPT_TEMPLATES 里的内置模板。
"""
from .core import get_db


def get_prompt_template(mode: str) -> str | None:
    conn = get_db()
    row = conn.execute(
        "SELECT template FROM prompt_templates WHERE mode = ?", (mode,)
    ).fetchone()
    conn.close()
    return row["template"] if row else None


def set_prompt_template(mode: str, template: str) -> None:
    conn = get_db()
    conn.execute(
        """INSERT INTO prompt_templates (mode, template, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(mode) DO UPDATE SET
             template = excluded.template, updated_at = excluded.updated_at""",
        (mode, template),
    )
    conn.commit()
    conn.close()


def delete_prompt_template(mode: str) -> None:
    conn = get_db()
    conn.execute("DELETE FROM prompt_templates WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()
