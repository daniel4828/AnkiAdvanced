#!/bin/bash
# 一次性数据修复（#728）：把误落到 2026-08-14 锁定牌组的 9 个生词搬到今天。
# 用完请删除本文件。
set -euo pipefail

SRV="anki@207.180.204.135"
DB="/home/anki/AnkiAdvanced/data/srs.db"

echo "== 步骤 1/4：再做一次备份快照（几秒） =="
ssh "$SRV" "sqlite3 $DB \".backup /home/anki/AnkiAdvanced/data/backups/pre-728-move-\$(date +%Y%m%d-%H%M%S).db\" && ls -lh /home/anki/AnkiAdvanced/data/backups/pre-728-move-*.db"

echo
echo "== 步骤 2/4：搬动前的状态 =="
ssh "$SRV" "sqlite3 -header -column $DB \"SELECT d.name AS 牌组, COUNT(*) AS 卡片数 FROM cards c JOIN decks d ON d.id=c.deck_id WHERE c.deck_id IN (1603,1604,1605,1607,1608,1609) AND c.deleted_at IS NULL GROUP BY d.name;\""

echo
echo "== 步骤 3/4：把 08-14 的新卡搬到 08-13（deck_id 1607→1603 / 1608→1604 / 1609→1605，due 一起改） =="
ssh "$SRV" "sqlite3 $DB \"
UPDATE cards SET deck_id=1603, due='2026-08-13' WHERE deck_id=1607 AND state='new' AND due='2026-08-14' AND deleted_at IS NULL;
UPDATE cards SET deck_id=1604, due='2026-08-13' WHERE deck_id=1608 AND state='new' AND due='2026-08-14' AND deleted_at IS NULL;
UPDATE cards SET deck_id=1605, due='2026-08-13' WHERE deck_id=1609 AND state='new' AND due='2026-08-14' AND deleted_at IS NULL;
\""
echo "搬动完成。"

echo
echo "== 步骤 4/4：搬动后的状态 + 重启服务清空内存里的会话队列 =="
ssh "$SRV" "sqlite3 -header -column $DB \"SELECT d.name AS 牌组, COUNT(*) AS 卡片数 FROM cards c JOIN decks d ON d.id=c.deck_id WHERE c.deck_id IN (1603,1604,1605,1607,1608,1609) AND c.deleted_at IS NULL GROUP BY d.name;\""
# 会话队列每个 Anki 日只在内存里构建一次，直接改数据库它是不知道的，
# 必须重启服务才会重建。sudo 不可用时不算失败：cron 每 2 分钟的自动部署
# 也会重启（PR #729 刚合并，这一轮必定重启）。
ssh "$SRV" "sudo -n systemctl restart ankiadvanced && sleep 3 && systemctl is-active ankiadvanced" \
  || echo "（无法直接重启，没关系：等两分钟自动部署会重启服务，然后刷新页面即可）"

echo
echo "全部完成。请把以上全部输出发给 Claude。之后刷新浏览器页面。"
