"""复习收尾提醒（议题 #701）。

Daniel 清空当天队列后，被他评为 Again 的卡片还留在学习步骤里（1m 10m 1d 3d），
分批重新到期。他离开界面就无从知道它们什么时候回来。本模块由服务器 cron
定期调用：当**今天剩下的复习可以一口气做完**时发一封邮件，且每个 Anki 日
最多发一次。

判定逻辑全在 database.due_notification_status()，这里只负责去重与发信。
"""

import logging
import os

import database
import podcast

logger = logging.getLogger(__name__)

# 记录上次发送提醒的 Anki 日（ISO 日期），保证每天最多一封。
LAST_SENT_KEY = "due_notify_last_day"


def _build_email_html(status: dict) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "https://powerdaniel3000.duckdns.org")
    count = status["due_now"]
    return f"""
    <html><body style="font-family:sans-serif;max-width:640px">
      <h2>复习收尾：{count} 张卡片等你</h2>
      <p style="font-size:15px;line-height:1.7">
        今天你评为 Again 的卡片已经<b>全部</b>重新到期，现在一次就能做完，
        不会做到一半又要等下一个学习步骤。
      </p>
      <p><a href="{base}/">打开复习</a></p>
    </body></html>
    """


def check_and_notify(force: bool = False) -> dict:
    """跑一次判定，满足条件就发邮件。返回判定明细 + 本次是否发送。

    force=True 跳过"每天一封"的去重，供手动测试用；仍然要求条件成立。
    """
    status = database.due_notification_status()
    today = database.anki_today().isoformat()
    already = database.get_app_setting(LAST_SENT_KEY)

    result = {**status, "sent": False, "already_sent_today": already == today}

    if not status["ready"]:
        logger.info("due-notify: 条件未满足 %s", status)
        return result
    if already == today and not force:
        logger.info("due-notify: 今天（%s）已经发过，跳过", today)
        return result

    # SMTP 未配置时 send_mail 返回 False —— 那是"跳过"不是失败，所以也不写
    # 标记：等配置好了当天仍然能收到提醒。
    sent = podcast.send_mail(
        f"复习收尾：{status['due_now']} 张卡片已重新到期",
        _build_email_html(status),
        context="due-notify",
    )
    if sent:
        database.set_app_setting(LAST_SENT_KEY, today)
    result["sent"] = sent
    return result
