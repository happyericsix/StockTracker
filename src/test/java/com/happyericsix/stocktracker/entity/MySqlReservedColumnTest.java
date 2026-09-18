package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.Transient;
import org.junit.jupiter.api.Test;

import java.io.File;
import java.lang.reflect.Field;
import java.net.URL;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 实体列名不得撞上 MySQL 保留字。
 *
 * <h3>为什么必须有这条用例（它是被真库教出来的）</h3>
 * 第一次把这一版代码跑在真 MySQL 上时，{@code create table paper_trade_traces} 直接语法报错：
 * 里面有列叫 {@code signal}，而 {@code SIGNAL} 是 MySQL 的保留字（存储程序用）。
 * 测试库是 H2，**接受**这个名字，所以 200 多条用例全绿、DDL 看起来一切正常。
 *
 * <p>后果不是"报个错"，而是**痕迹表永远建不出来**：结算照旧成交、账户照旧更新，
 * 只有痕迹写不进去 —— 界面上的痕迹永远是空的，而"为什么今天没成交"这个问题
 * 从此再也答不出来。这类失败必须靠一次静态检查挡住，不能指望下次记得。
 *
 * <p>保留字清单不是手写的：{@code src/test/resources/mysql8-reserved-words.txt} 由
 * 真库导出（{@code SELECT WORD FROM INFORMATION_SCHEMA.KEYWORDS WHERE RESERVED=1}，
 * MySQL 8.0 共 262 个），升级 MySQL 后重新导出即可。
 */
class MySqlReservedColumnTest {

    private static final String RESERVED_RESOURCE = "/mysql8-reserved-words.txt";

    /**
     * 探针：用一个明确违法的列名证明"检测本身有效"—— 检测不出来时，全表扫描的绿灯毫无意义。
     *
     * <p><b>刻意不标 {@code @Entity}</b>：本类所在的包正是实体扫描范围，
     * 一个真的 {@code @Entity}（哪怕没有 {@code @Id}）会被 Hibernate 纳入元模型，
     * 于是**每一个** {@code @SpringBootTest} 都因为"No identifier specified"起不来 ——
     * 一个只做反射的探针去打翻 13 个上下文用例，是最不划算的连带损伤。
     * 检测逻辑本身只读 {@code @Column}/{@code @JoinColumn}，不需要这个注解。
     */
    static class SignalProbe {
        @Column(name = "signal")
        String signal;
    }

    @Test
    void theDetectorActuallyDetects() {
        List<String> violations = reservedViolations(SignalProbe.class, reservedWords());
        assertTrue(violations.stream().anyMatch(v -> v.endsWith("signal")),
                "检测逻辑必须能认出 signal（否则下面那条断言等于没跑）：" + violations);
    }

    @Test
    void theReservedListIsLoadedAndContainsTheKnownOffenders() {
        Set<String> words = reservedWords();
        assertTrue(words.size() > 200, "保留字清单没读到？实际只有 " + words.size() + " 个");
        assertTrue(words.contains("signal") && words.contains("trigger"),
                "清单里必须包含真的踩过的这两个词");
    }

    @Test
    void noEntityColumnCollidesWithMySqlReservedWords() throws Exception {
        Set<String> reserved = reservedWords();
        List<String> violations = new ArrayList<>();
        int inspected = 0;

        for (Class<?> entity : entityClasses()) {
            inspected++;
            violations.addAll(reservedViolations(entity, reserved));
        }

        // 扫到 0 个实体时这条用例会"通过"，那比没有用例更糟 —— 所以先钉住扫描有效。
        assertTrue(inspected >= 5, "扫描到的实体太少（" + inspected + "），目录定位可能失效");
        assertTrue(violations.isEmpty(),
                "这些列名是 MySQL 保留字，真库上建表会直接语法报错（H2 不会报）：" + violations);
    }

    /** 某一个实体里所有撞上保留字的列（列名 → 违规描述）。 */
    private static List<String> reservedViolations(Class<?> entity, Set<String> reserved) {
        List<String> violations = new ArrayList<>();
        for (Class<?> type = entity; type != null && type != Object.class; type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (field.isSynthetic() || java.lang.reflect.Modifier.isStatic(field.getModifiers())
                        || field.isAnnotationPresent(Transient.class)) {
                    continue;
                }
                String column = columnName(field);
                if (column != null && reserved.contains(column.toLowerCase(Locale.ROOT))) {
                    violations.add(entity.getSimpleName() + "." + field.getName() + " → " + column);
                }
            }
        }
        return violations;
    }

    /**
     * 字段实际映射到的列名：显式 {@code @Column}/{@code @JoinColumn} 优先，
     * 否则用 Hibernate/Spring Boot 的默认口径（驼峰 → 下划线）。
     */
    private static String columnName(Field field) {
        JoinColumn join = field.getAnnotation(JoinColumn.class);
        if (join != null && !join.name().isBlank()) {
            return join.name();
        }
        Column column = field.getAnnotation(Column.class);
        if (column != null && !column.name().isBlank()) {
            return column.name();
        }
        return camelToSnake(field.getName());
    }

    /** Spring Boot 默认的物理命名策略：{@code lastEvalAt → last_eval_at}。 */
    private static String camelToSnake(String name) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (Character.isUpperCase(c)) {
                if (i > 0) {
                    out.append('_');
                }
                out.append(Character.toLowerCase(c));
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    private static Set<String> reservedWords() {
        try (var stream = MySqlReservedColumnTest.class.getResourceAsStream(RESERVED_RESOURCE)) {
            if (stream == null) {
                throw new IllegalStateException("找不到保留字清单 " + RESERVED_RESOURCE);
            }
            Set<String> words = new LinkedHashSet<>();
            new java.io.BufferedReader(new java.io.InputStreamReader(stream, java.nio.charset.StandardCharsets.UTF_8))
                    .lines()
                    .map(String::trim)
                    .filter(line -> !line.isEmpty() && !line.startsWith("#"))
                    .map(line -> line.toLowerCase(Locale.ROOT))
                    .forEach(words::add);
            return words;
        } catch (Exception e) {
            throw new IllegalStateException("读取保留字清单失败", e);
        }
    }

    /** 编译产物里的全部实体类（跳过内部类：{@code $} 结尾的那些是探针与辅助类）。 */
    private static List<Class<?>> entityClasses() throws Exception {
        // 必须从**主产物**定位：测试类自己也在同名包里，用 getResource 会先命中
        // target/test-classes（那里面一个实体都没有），于是扫描静默扫到 0 个类。
        File root = new File(Strategy.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        File dir = new File(root, "com/happyericsix/stocktracker/entity");
        File[] files = dir.listFiles((d, name) -> name.endsWith(".class") && !name.contains("$"));
        if (files == null) {
            throw new IllegalStateException("无法列出实体包目录 " + dir);
        }
        List<Class<?>> classes = new ArrayList<>();
        for (File file : files) {
            String simple = file.getName().substring(0, file.getName().length() - ".class".length());
            Class<?> type = Class.forName("com.happyericsix.stocktracker.entity." + simple);
            if (type.isAnnotationPresent(Entity.class)) {
                classes.add(type);
            }
        }
        assertFalse(classes.isEmpty(), "实体包目录里一个 @Entity 都没有：" + dir);
        return classes;
    }
}
