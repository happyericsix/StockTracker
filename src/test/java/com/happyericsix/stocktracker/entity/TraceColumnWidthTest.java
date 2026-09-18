package com.happyericsix.stocktracker.entity;

import com.happyericsix.stocktracker.service.ExecutionContract;
import jakarta.persistence.Column;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 痕迹里那些**封闭集列的宽度必须放得下契约产出的任何值**。
 *
 * <h3>为什么要有这条用例（它是被一次回滚教出来的）</h3>
 * 归一函数认不出来时写的是 {@code unknown_<原值>}，空值则是 {@code unknown_unspecified}
 * —— 19 个字符。而契约声明的上限是 {@code MAX_ENUM_CHARS = 32}。
 * 但 {@code adjust_mode} 这一列原来是 {@code varchar(16)}。
 *
 * <p>后果比"少一行记录"严重得多：这条 INSERT 跑在**结算事务里**，
 * 约束冲突在 flush 时抛出 → 整个事务回滚 → 当天那笔结算（含净值快照、客观事实）
 * 一起丢掉。触发条件只是一条没有指纹的降级响应。
 *
 * <p>所以这里把"列宽 ≥ 契约上限"变成硬约束：以后谁把某个封闭集列改窄，
 * 或者新加一列时随手写个 16，这条用例会立刻红 —— 而不是等到某天结算整笔回滚。
 */
class TraceColumnWidthTest {

    /** 值由 ExecutionContract 的归一函数产出的那些列（名字与封闭集一一对应）。 */
    private static final List<String> CLOSED_SET_FIELDS = List.of(
            "settlementKind", "decisionMode", "trigger", "decision",
            "skipReason", "signal", "adjustMode", "fillBasis");

    @Test
    void closedSetColumnsAreWideEnoughForTheContractOutput() throws Exception {
        List<String> tooNarrow = new ArrayList<>();
        int inspected = 0;

        for (String name : CLOSED_SET_FIELDS) {
            Field field = PaperTradeTrace.class.getDeclaredField(name);
            columnLength(field, name).ifPresentOrElse(length -> {
                if (length < ExecutionContract.MAX_ENUM_CHARS) {
                    tooNarrow.add(name + " → varchar(" + length + ")");
                }
            }, () -> tooNarrow.add(name + " → 没有 @Column 宽度（默认 255，需要显式声明）"));
            inspected++;
        }

        assertTrue(inspected == CLOSED_SET_FIELDS.size(), "字段清单与实体脱节了");
        assertTrue(tooNarrow.isEmpty(),
                "这些封闭集列放不下 unknown_unspecified（19 字符）/ MAX_ENUM_CHARS（"
                        + ExecutionContract.MAX_ENUM_CHARS + "）的值，会导致结算整笔回滚：" + tooNarrow);
        // 契约本身的上限也必须容得下那个空值兜底，否则源头就该改
        assertTrue("unknown_unspecified".length() <= ExecutionContract.MAX_ENUM_CHARS,
                "unknown_unspecified 比 MAX_ENUM_CHARS 还长，空值兜底会被截断");
    }

    /** 列宽：显式 {@code length} 优先；缺省 255（见 JPA 默认）—— 但缺省也要显式写出来。 */
    private static java.util.Optional<Integer> columnLength(Field field, String name) {
        Column column = field.getAnnotation(Column.class);
        if (column == null) {
            return java.util.Optional.empty();
        }
        return java.util.Optional.of(column.length());
    }
}
