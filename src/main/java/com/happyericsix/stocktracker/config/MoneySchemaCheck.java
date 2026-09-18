package com.happyericsix.stocktracker.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 启动时检查"钱的列到底是不是 DECIMAL"。
 *
 * <h3>为什么需要它（这不是洁癖，是一个真实的静默缺口）</h3>
 * 项目把金额迁到了 {@code BigDecimal}，实体上写了 {@code precision/scale}。
 * 但 {@code spring.jpa.hibernate.ddl-auto=update} **只加列、不改列的类型**：
 * 已有的 MySQL 库里那些列仍然是 {@code DOUBLE}，而代码、测试、日志看起来一切正常 ——
 * 精度只在"逐笔对账"时以 1e-13 的形式露出来，那时候已经很难查了。
 *
 * <p>所以启动时查一次 JDBC 元数据，把不一致的列连同**可直接执行的 ALTER**打出来。
 * 刻意只 WARN 不中断：这是开发/部署动作，不是运行时错误；但绝不静默 ——
 * 一个"以为迁了、其实没迁"的钱包比没迁更危险。
 *
 * <p>用标准 JDBC 元数据（而不是 {@code INFORMATION_SCHEMA} 的方言查询）：
 * 这样它在 H2（测试）与 MySQL（运行）上走的是同一段代码，也就能被测试覆盖到。
 */
@Component
public class MoneySchemaCheck implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(MoneySchemaCheck.class);

    /** 需要是 DECIMAL 的表与列（与实体上的 precision/scale 对应）。 */
    private static final List<TableColumns> EXPECTED = List.of(
            new TableColumns("paper_accounts", List.of(
                    "initial_capital", "cash", "equity", "shares", "avg_cost",
                    "high_watermark", "last_price")),
            new TableColumns("paper_trades", List.of("price", "shares", "amount")));

    private final DataSource dataSource;

    public MoneySchemaCheck(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    @Override
    public void run(String... args) {
        try {
            List<String> problems = findNonDecimalColumns(dataSource);
            if (problems.isEmpty()) {
                log.info("金额列检查通过：{} 张表的金额字段都是定点数（DECIMAL）",
                        EXPECTED.size());
                return;
            }
            log.warn("金额列仍是浮点/其它类型（{} 处）。ddl-auto=update **不会**改已有列的类型，"
                    + "所以精度仍受浮点限制 —— 请执行下面的 DDL 后重启：\n{}",
                    problems.size(), String.join("\n", problems));
        } catch (Exception e) {
            // 元数据读不到（权限、非常规数据源）不该挡住启动，但要说出来：
            // "检查没跑成"与"检查通过"是两件事
            log.warn("金额列检查未能执行（不影响启动）: {}", e.getMessage());
        }
    }

    /**
     * 返回"不是定点数"的列清单（附带可执行的 ALTER）。
     *
     * <p>包级可见：单测直接调它，不必真的启动一个坏 schema。
     */
    List<String> findNonDecimalColumns(DataSource source) throws Exception {
        List<String> problems = new ArrayList<>();
        try (Connection connection = source.getConnection()) {
            DatabaseMetaData meta = connection.getMetaData();
            for (TableColumns table : EXPECTED) {
                for (String column : table.columns()) {
                    String type = columnType(meta, table.table(), column);
                    if (type == null) {
                        continue;   // 表或列不存在：建表交给 Hibernate，这里不越权
                    }
                    if (!type.toUpperCase(Locale.ROOT).contains("DECIMAL")
                            && !type.toUpperCase(Locale.ROOT).contains("NUMERIC")) {
                        problems.add(String.format(
                                "  %s.%s 当前是 %s → ALTER TABLE %s MODIFY %s DECIMAL(18,%d);",
                                table.table(), column, type, table.table(), column,
                                scaleFor(column)));
                    }
                }
            }
        }
        return problems;
    }

    /** 列类型名（读不到返回 null）。表名大小写在不同库上不一致，所以两种都试。 */
    private static String columnType(DatabaseMetaData meta, String table, String column) throws Exception {
        for (String candidate : List.of(table, table.toUpperCase(Locale.ROOT))) {
            try (ResultSet rs = meta.getColumns(null, null, candidate, null)) {
                while (rs.next()) {
                    if (column.equalsIgnoreCase(rs.getString("COLUMN_NAME"))) {
                        return rs.getString("TYPE_NAME");
                    }
                }
            }
        }
        return null;
    }

    /** 与 {@code Money} 的口径一致：金额/净值 2 位，价格/股数 4 位。 */
    private static int scaleFor(String column) {
        return switch (column) {
            case "cash", "equity", "initial_capital", "amount" -> 2;
            default -> 4;
        };
    }

    private record TableColumns(String table, List<String> columns) {
    }
}
