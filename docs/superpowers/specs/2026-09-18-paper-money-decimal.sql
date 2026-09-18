-- 一次性迁移：模拟盘的钱从 DOUBLE 迁到定点数 DECIMAL
--
-- ⚠️ 结论先行（2026-09-18 实测更正）：
--   这一版代码第一次真正跑在真 MySQL 上时（spring-boot:run，Hibernate 7.2），
--   ddl-auto=update **自己就把这些列改成了 DECIMAL**：启动日志里有
--     alter table paper_trades modify column amount decimal(18,2) not null
--   之后 MoneySchemaCheck 报 "金额列检查通过：2 张表的金额字段都是定点数（DECIMAL）"，
--   核对 INFORMATION_SCHEMA 也确认全部为 decimal（金额 2 位、价格与股数 4 位）。
--   也就是说：**正常情况下不需要手工执行本脚本**。
--
--   本脚本现在的用途降级为两点：
--     1) 兜底：如果你的 Hibernate 版本/配置不做列类型变更（旧版本确实只加列），
--        或者你要在没有应用启动的情况下直接改库；
--     2) 核对：脚本末尾那条自检查询随时可以跑，返回空即表示已经是定点数。
--
--   （原文写的"update 只加列、不改类型"对 Hibernate 7.2 不成立，故更正。
--   注意 ddl-auto=update 会顺带加上 NOT NULL —— 生产库若有历史 NULL 值，
--   这类改列可能失败，那种情况下仍需手工执行本脚本。）
--
-- 为什么这件事重要：
--   代码、测试、日志在 DOUBLE 列上看起来一切正常；精度只在"逐笔对账"时
--   以 1e-13 的形式露出来（见 MoneyTest 的那条断言）。
--
-- 说明：
--   · 本文件是**手工执行**的一次性脚本（项目没有 Flyway/Liquibase）。
--   · 小数位与 Money/MoneyPolicy 一致：金额 2 位、价格与股数 4 位。
--   · 已有数据按 MySQL 的转换规则四舍五入到目标小数位，不需要回填。
--
-- 执行前建议先备份 paper_accounts / paper_trades。

ALTER TABLE paper_accounts
    MODIFY initial_capital DECIMAL(18, 2),
    MODIFY cash            DECIMAL(18, 2),
    MODIFY equity          DECIMAL(18, 2),
    MODIFY shares          DECIMAL(18, 4),
    MODIFY avg_cost        DECIMAL(18, 4),
    MODIFY high_watermark  DECIMAL(18, 4),
    MODIFY last_price      DECIMAL(18, 4);

ALTER TABLE paper_trades
    MODIFY price  DECIMAL(18, 4),
    MODIFY shares DECIMAL(18, 4),
    MODIFY amount DECIMAL(18, 2);

-- 迁移后的自检：这条查询应当一行都不返回（返回的行就是还没迁过来的列）
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('paper_accounts', 'paper_trades')
  AND COLUMN_NAME IN ('initial_capital', 'cash', 'equity', 'shares', 'avg_cost',
                      'high_watermark', 'last_price', 'price', 'amount')
  AND DATA_TYPE NOT IN ('decimal', 'numeric');
