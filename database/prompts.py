"""提示词版本库（issue #610，原单份自定义模板见 #581）。

每个故事模式（story/qa/expository/podcast）可以保存多个命名的提示词版本
（prompt_presets 表），其中最多一行 is_active=1 表示当前生效版本。没有
生效行 = 使用 ai.DEFAULT_PROMPT_TEMPLATES 里的内置模板。

旧的 prompt_templates 表（每模式一份自定义模板，无名字）仍保留在
schema.sql 里但不再写入——数据已在 database/core.py 的 init_db() 迁移里
搬到 prompt_presets（名为 "Saved" 的生效版本）。
"""
from .core import get_db


def get_prompt_template(mode: str) -> str | None:
    """当前生效 preset 的模板全文；该 mode 没有生效版本时返回 None。"""
    conn = get_db()
    row = conn.execute(
        "SELECT template FROM prompt_presets WHERE mode = ? AND is_active = 1",
        (mode,),
    ).fetchone()
    conn.close()
    return row["template"] if row else None


def list_prompt_presets(mode: str) -> list[dict]:
    """该 mode 下所有已保存版本的概要（不含模板全文），按名称排序。"""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, name, is_active, updated_at FROM prompt_presets
           WHERE mode = ? ORDER BY name COLLATE NOCASE""",
        (mode,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prompt_preset(preset_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM prompt_presets WHERE id = ?", (preset_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_prompt_preset(mode: str, name: str, template: str) -> int:
    """新建一个版本并立即设为该 mode 的生效版本，返回新 id。

    重名（同 mode+name）时 sqlite3.IntegrityError 会向上抛出，由路由层
    捕获转成 409。
    """
    conn = get_db()
    conn.execute("UPDATE prompt_presets SET is_active = 0 WHERE mode = ?", (mode,))
    cur = conn.execute(
        """INSERT INTO prompt_presets (mode, name, template, is_active, updated_at)
           VALUES (?, ?, ?, 1, datetime('now'))""",
        (mode, name, template),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_prompt_preset(preset_id: int, name: str | None = None,
                          template: str | None = None) -> None:
    """只更新传入的字段；重名（改名冲突）由 sqlite3.IntegrityError 向上抛出。"""
    conn = get_db()
    if name is not None:
        conn.execute(
            "UPDATE prompt_presets SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, preset_id),
        )
    if template is not None:
        conn.execute(
            "UPDATE prompt_presets SET template = ?, updated_at = datetime('now') WHERE id = ?",
            (template, preset_id),
        )
    conn.commit()
    conn.close()


def activate_prompt_preset(preset_id: int) -> None:
    """将 preset_id 设为其 mode 下唯一生效版本（同一事务内切换）。"""
    conn = get_db()
    row = conn.execute(
        "SELECT mode FROM prompt_presets WHERE id = ?", (preset_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return
    conn.execute("UPDATE prompt_presets SET is_active = 0 WHERE mode = ?", (row["mode"],))
    conn.execute(
        "UPDATE prompt_presets SET is_active = 1, updated_at = datetime('now') WHERE id = ?",
        (preset_id,),
    )
    conn.commit()
    conn.close()


def delete_prompt_preset(preset_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM prompt_presets WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()


def deactivate_prompt_presets(mode: str) -> None:
    """该 mode 下全部版本设为非生效——故事生成回落内置默认模板。"""
    conn = get_db()
    conn.execute("UPDATE prompt_presets SET is_active = 0 WHERE mode = ?", (mode,))
    conn.commit()
    conn.close()
