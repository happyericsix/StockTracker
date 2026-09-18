package com.happyericsix.stocktracker.config;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 启动检查（{@link MoneySchemaCheck}）**在有真实 schema 的环境里真的跑得动**。
 *
 * <h3>为什么这条必须存在</h3>
 * 这个检查的全部价值在于"迁移没生效时会响"。而它的失效方式有两种，都很安静：
 * ① 元数据读法不对（表名大小写、方言差异）→ 永远查不到列 → 永远不报警；
 * ② 实体上写了 {@code precision/scale}，但 Hibernate 实际建出来的列不是定点数。
 * 两者都只能靠"真跑一次、真读到列"来发现 —— 所以这里在测试数据源（H2）上验证：
 * 期望的列**都存在**，且**都是定点数**（也就等于验证了实体的 precision/scale 落到了 DDL）。
 */
@SpringBootTest
class MoneySchemaCheckTest {

    @Autowired
    private MoneySchemaCheck check;

    @Autowired
    private javax.sql.DataSource dataSource;

    @Test
    void everyMoneyColumnIsActuallyADecimal() throws Exception {
        List<String> problems = check.findNonDecimalColumns(dataSource);

        assertTrue(problems.isEmpty(),
                "金额列不是定点数（浮点会让净值恒等式无法零容差成立）：\n" + String.join("\n", problems));
    }

    @Test
    void theCheckCanSeeTheColumnsAtAll() throws Exception {
        // 反向验证：如果列读不到（表名/dialect 读法不对），上面的"没问题"就是假绿。
        // 这里直接确认至少读到了 paper_accounts 的 cash —— 它的类型名必须能被取出来。
        java.sql.DatabaseMetaData meta;
        try (java.sql.Connection connection = dataSource.getConnection()) {
            meta = connection.getMetaData();
        }
        boolean found = false;
        for (String candidate : List.of("paper_accounts", "PAPER_ACCOUNTS")) {
            try (java.sql.ResultSet rs = meta.getColumns(null, null, candidate, null)) {
                while (rs.next()) {
                    if ("cash".equalsIgnoreCase(rs.getString("COLUMN_NAME"))) {
                        found = true;
                    }
                }
            }
        }
        assertTrue(found, "读不到 paper_accounts.cash —— 那么启动检查永远查不到问题（假绿）");
    }
}
