-- 一次性迁移：模拟盘的钱从 DOUBLE 迁到定点数 DECIMAL
--
-- 为什么要跑：
--   代码已经用 BigDecimal 结算（Money.java / 实体上的 precision+scale），
--   但 spring.jpa.hibernate.ddl-auto=update **只加列、不改列的类型**：
--   已有的库里这些列仍然是 DOUBLE，而代码、测试、日志看起来一切正常。
--   精度只在"逐笔对账"时以 1e-13 的形式露出来（见 MoneyTest 的那条断言）。
--
-- 谁会发现该跑：
--   启动时 MoneySchemaCheck 会查一次 JDBC 元数据；列不是定点数就 WARN，
--   并把对应的 ALTER 打在日志里。它只告警、不中断启动。
--
-- 说明：
--   · 本文件是**手工执行**的一次性脚本（项目没有 Flyway/Liquibase）。
--   · 小数位与 Money/ MoneyPolicy 一致：金额 2 位、价格与股数 4 位。
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
