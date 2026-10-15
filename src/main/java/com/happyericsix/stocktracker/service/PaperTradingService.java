package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.MemoryLessonRequest;
import com.happyericsix.stocktracker.dto.MemoryLessonsRequest;
import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperEquityResponse;
import com.happyericsix.stocktracker.dto.PaperOverviewResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperAuthorization;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperPositionLot;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperAuthorizationRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

@Service
public class PaperTradingService {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingService.class);

    private static final double DEFAULT_INITIAL_CAPITAL = 100000.0;
    /**
     * 佣金兜底默认：万 2.5（2026 年散户常见档）。**它只是"档案和策略都没说"时的兜底**——
     * 正常路径是用户档案 commissionPct（与券商的真实约定），其次才是旧策略配置里的
     * risk.commission_pct（兼容历史）。佣金没有官方统一值，所以永远允许被覆盖。
     */
    private static final double DEFAULT_COMMISSION_PCT = 0.025;
    private static final double DEFAULT_SLIPPAGE_PCT = 0.1;
    /** 卖出印花税兜底（查表失败时用；正常路径走 MarketRulesService 官方生效日期表） */
    private static final double DEFAULT_STAMP_TAX_PCT = 0.05;
    /** 过户费兜底（双边，查表失败时用） */
    private static final double DEFAULT_TRANSFER_FEE_PCT = 0.001;
    private static final double DEFAULT_MIN_COMMISSION_YUAN = 5.0;
    private static final String DEFAULT_POSITION_TYPE = "full";
    private static final double DEFAULT_SIZE_PCT = 100.0;

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperTraceService paperTraceService;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;
    /** 客观事实写入（W1）。允许为 null：单测没有替身，且"记不进记忆"不该影响结算。 */
    private final MemoryFactService memoryFactService;
    private final MemoryService memoryService;
    /** 盘后复盘报告。允许为 null（单测），失败只是少一条消息。 */
    /** 样本外验证（周频）。允许为 null：单测里没有这个替身，周任务本身也不该被单测触发。 */
    private final StrategyService strategyService;
    /** 预期登记/回填。允许为 null（单测）；失败只是少一次回填。 */
    private final ExpectationService expectationService;
    /** 策略哨兵（健康监控 Phase 1）。允许为 null（单测）；它是结算后的附加题，绝不影响结算。 */
    private final StrategyHealthWatchService healthWatchService;
    /** 盘中授权（authorization 模式）：委员会开会产出边界，盘中规则引擎在边界内执行。 */
    private final PaperAuthorizationRepository paperAuthorizationRepository;
    /** 组合层：单日回撤熔断（挡新开仓）。允许为 null（单测）：调用处 fail-open。 */
    private final PortfolioService portfolioService;

    /**
     * 新闻知识组装（agent 互通 P3，可空）：为委员会/盯盘提供信息面证据。
     * 可空是为了单测可以不装它 —— 信息面是增强证据，缺失时决策照旧（fail-open）。
     */
    private final NewsBriefingService newsBriefingService;
    /** 经验回流（P4）：给委员会/盯盘组装"这条策略自己的成交成绩单"。可选，null 判断兜住。 */
    private final DecisionQualityService decisionQualityService;
    /** 周反思（P2）：教训的落库与回读。可选，null 判断兜住（反思是旁路）。 */
    private final MemoryLessonService memoryLessonService;
    /** 成交推送旁路。允许为 null（单测）：推送失败绝不影响结算。 */
    private final PushService pushService;
    private final MessageService messageService;
    /**
     * 持仓批次账本（2026-10-15）：买入建批、卖出先进先出消耗、T+1/红利税按批次。
     * 允许为 null（单测）：null 时卖出走 legacy 语义（lastBuyBar 整仓 T+1）。
     */
    private final com.happyericsix.stocktracker.repository.PaperPositionLotRepository lotRepository;
    /**
     * 官方费率与板块规则（按生效日期查表）。允许为 null（单测）：null 时退回
     * 本类顶部的兜底常量——单测的确定性优先，生产行为以注册表为准。
     */
    private final MarketRulesService marketRulesService;
    /**
     * 涨跌停挡单要用的日线昨收与股票名称（ST 识别）。允许为 null（单测）：
     * 拿不到昨收就不挡（fail-open），挡单是真实性增强，不是结算的生存线。
     */
    private final StockDataGateway stockDataGateway;
    private final ObjectMapper mapper = new ObjectMapper();
    private final TransactionTemplate transactionTemplate;

    // ===== 盘中盯盘 agent（事件驱动，2026-10-09）=====
    /** 总开关：关掉后 agent 模式盘中回到"规则+授权"的旧行为。 */
    @Value("${paper.intraday-watch.enabled:true}")
    private boolean intradayWatchEnabled;
    /**
     * 当日往返护栏（默认开，可按部署关闭）：agent 模式盘中授权通道当天已有成交后
     * 不再执行新买入。它防的是"参数互搏型"当日来回打脸（2026-10-14 实测）；
     * 但日内多笔（做T/分批）是合法策略形态——这类部署把它关掉即可。
     * 只作用于 agent 模式盘中买入；规则模式多笔交易、止损卖出均不受影响。
     */
    @Value("${paper.intraday.roundtrip-guard-enabled:true}")
    private boolean roundtripGuardEnabled;
    /** 每策略每日盯盘 LLM 调用预算（成本止损线，2026-10-09 产品决策：平衡档 20 次）。 */
    @Value("${paper.intraday-watch.daily-budget:20}")
    private int intradayWatchDailyBudget;
    /** 同一事件类型的冷却窗口：一次急涨往往连着几根 bar 都"在触发"，不冷却会被同一件事叫醒多次。 */
    private static final long WATCH_EVENT_COOLDOWN_MS = 15 * 60 * 1000L;
    /** 事件冷却表：strategyId:eventType → 上次触发时刻。重启丢失无害（最多多问几次模型）。 */
    private final Map<String, Long> watchEventLastFired = new ConcurrentHashMap<>();
    /** 盯盘调用计数：strategyId:tradeDate → 次数。重启归零无害（预算是止损线不是账单）。 */
    private final Map<String, Integer> watchCallCount = new ConcurrentHashMap<>();

    // ===== 分红除权（公司行动，2026-10-14）=====
    /** 分红数据缓存：除权日提前公告，1 小时新鲜度足够（Python 侧还有 12h 源缓存）。 */
    private final com.github.benmanes.caffeine.cache.Cache<String, List<DividendInfo>> dividendCache =
            com.github.benmanes.caffeine.cache.Caffeine.newBuilder()
                    .expireAfterWrite(java.time.Duration.ofHours(1))
                    .maximumSize(64)
                    .build();

    /** 一条分红送配记录（每股口径）。 */
    private record DividendInfo(LocalDate exDate, BigDecimal cashPerShare,
                                BigDecimal stockPerShare, String plan) {}

    /**
     * 单一构造函数：**不要为了"测试方便"再加几个重载**。
     * Spring 遇到多个构造函数且没有 {@code @Autowired} 时会去找无参构造，直接起不来
     * （实测踩过：三个重载 → {@code NoSuchMethodException: <init>()} → 整个应用上下文挂掉）。
     * 可选依赖（报告/验证/预期）允许为 null，用 null 判断兜住，而不是靠另一个构造函数。
     */
    public PaperTradingService(StrategyRepository strategyRepository,
                               PaperAccountRepository paperAccountRepository,
                               PaperTradeRepository paperTradeRepository,
                               PaperTraceService paperTraceService,
                               PaperEquitySnapshotRepository paperEquitySnapshotRepository,
                               StrategyClient strategyClient,
                               UserRepository userRepository,
                               PlatformTransactionManager transactionManager,
                               MemoryFactService memoryFactService,
                               MemoryService memoryService,
                               StrategyService strategyService,
                               ExpectationService expectationService,
                               StrategyHealthWatchService healthWatchService,
                               PaperAuthorizationRepository paperAuthorizationRepository,
                               PortfolioService portfolioService,
                               PushService pushService,
                               NewsBriefingService newsBriefingService,
                               DecisionQualityService decisionQualityService,
                               MemoryLessonService memoryLessonService,
                               MessageService messageService,
                               com.happyericsix.stocktracker.repository.PaperPositionLotRepository lotRepository,
                               MarketRulesService marketRulesService,
                               StockDataGateway stockDataGateway) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.paperTraceService = paperTraceService;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
        this.strategyService = strategyService;
        this.expectationService = expectationService;
        this.healthWatchService = healthWatchService;
        this.paperAuthorizationRepository = paperAuthorizationRepository;
        this.portfolioService = portfolioService;
        this.pushService = pushService;
        this.newsBriefingService = newsBriefingService;
        this.decisionQualityService = decisionQualityService;
        this.memoryLessonService = memoryLessonService;
        this.messageService = messageService;
        this.lotRepository = lotRepository;
        this.marketRulesService = marketRulesService;
        this.stockDataGateway = stockDataGateway;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.transactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /**
     * 不包 @Transactional：evaluateRealtimeStrategy 内部会阻塞调用 Python（最长 60s），
     * 若包在长事务里会占住数据库连接；改为各 repository 写操作走自身短事务。
     */
    public PaperAccountResponse startPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);

        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseGet(PaperAccount::new);
        initializeAccount(account, strategy);

        strategy.setPaperEnabled(true);
        account = paperAccountRepository.save(account);
        strategyRepository.save(strategy);

        // 启动即思考（2026-10-08）：agent 模式启动走**日线口径**的手动结算，而不是盘中路径。
        // 盘中路径在没有任何授权时只会留下一条 awaiting_authorization 然后什么都不做 ——
        // 实测（2026-10-06~08 国庆假期）启动后账户哑到下一个 15:30，盘中每 5 分钟
        // 重复一条"等待授权"。日线口径会触发委员会门控的 first_decision（从未开过会 ⇒ 开会），
        // 当场拿到第一份授权与第一次决策。当天没有 K 线（休市/数据未出）时，
        // 这次尝试只留一条 no_bar 痕迹，不浪费模型调用，等下一次日线结算。
        PaperAccount evaluated = isAgentMode(strategy)
                ? evaluateStrategy(strategyId, CnTime.today(), ExecutionContract.TRIGGER_MANUAL)
                : evaluateRealtimeStrategy(strategyId, ExecutionContract.TRIGGER_MANUAL);
        if (evaluated != null) {
            account = evaluated;
        }

        log.info("User {} started paper trading for strategy id={}", username, strategyId);
        return PaperAccountResponse.from(account);
    }

    @Transactional
    public void stopPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);
        strategy.setPaperEnabled(false);
        strategyRepository.save(strategy);
        log.info("User {} stopped paper trading for strategy id={}", username, strategyId);
    }

    public void evaluateDaily() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        LocalDate today = CnTime.today();
        for (Strategy strategy : strategies) {
            try {
                Long strategyId = strategy.getId();
                // 不把结算包进事务：evaluateStrategy 中段是阻塞的 Python 决策调用
                // （rule 端点最长 60s；agent 委员会一次 LLM 往返更长），包进事务等于
                // 占着一个连接等网络 —— 与 startPaper 的既定纪律一致（见其注释）。
                // 断点续跑的保证来自幂等守卫（existsByStrategyIdAndTradeDate、
                // lastBarTime、痕迹 dedupe 键），不来自事务回滚。
                PaperAccount settled = evaluateStrategy(strategyId, today);
                afterSettlement(strategy, settled, today);
            } catch (Exception e) {
                log.error("Paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    /**
     * 结算后的哨兵检查：15:30 主路径与补跑重试共用。
     *
     * <p>2026-10-10 起这里**不再发日报/简报**：消息中心收窄为"预警 + 系统告警"，
     * 复盘内容活在模拟盘页面（PaperReportController 实时取文本），同一件事
     * 不说第二遍。结算终轮失败的系统告警（{@link #alertSettlementFailed}）仍走消息。
     *
     * <p>哨兵刻意放在结算落库**之后**、独立 try/catch：判据再多也不许碰坏结算结果。
     * （它也只落库观察事件，不推消息 —— 页面上看。）
     */
    private void afterSettlement(Strategy strategy, PaperAccount settled, LocalDate today) {
        if (settled == null) {
            return;
        }
        if (healthWatchService != null) {
            try {
                healthWatchService.checkStrategy(strategy, settled, today);
            } catch (Exception e) {
                log.warn("Health watch failed for strategy id={}: {}", strategy.getId(), e.getMessage());
            }
        }
    }

    /**
     * 当天日线结算是否停在**中间态**（no_bar / data_unavailable）。
     *
     * <p>判据是当天最后一条日线痕迹：重试成功后新痕迹排在后面，中间态会被自然覆盖；
     * 所以"最后一条还是中间态"就等于"这一天的结论还没出来，报告再等等"。
     */
    private boolean dailySettlementTransient(Long strategyId, LocalDate today) {
        List<PaperTradeTrace> dailyTraces = paperTraceService.listForDay(strategyId, today).stream()
                .filter(trace -> ExecutionContract.SETTLEMENT_DAILY.equals(trace.getSettlementKind()))
                .toList();
        if (dailyTraces.isEmpty()) {
            return false;   // 连日线痕迹都没有（异常路径）：不算"仍失败"，告警交给异常本身
        }
        String lastSkip = dailyTraces.get(dailyTraces.size() - 1).getSkipReason();
        return ExecutionContract.SKIP_NO_BAR.equals(lastSkip)
                || ExecutionContract.SKIP_DATA_UNAVAILABLE.equals(lastSkip);
    }

    /**
     * 重试**当天结算失败**的策略（no_bar / data_unavailable）。
     *
     * <h3>为什么必须重试（2026-10-08 实测）</h3>
     * 日线源在 15:30 结算当口常还没发布当天的 bar（腾讯源当天 17:05 才有 10-08 的日线）。
     * 不重试的后果是这一天的结算**永远缺失**：净值曲线少一格、委员会没开成会、
     * 授权没落库 —— 模拟盘从启动那天起就"始终是初始资金 + N/A"。
     *
     * <p>只重试当天**确实失败过**的策略：成功结算过的绝不再碰 —— 重跑一次 agent
     * 结算就是再花一次委员会的钱（一次约 2 万 token），而它今天已经说过话了。
     * 成功后照常走报告与哨兵（与 15:30 主路径同一份 {@link #afterSettlement}）。
     *
     * <p>终轮（17:10 / 手动补跑）之后仍失败的策略，推一条系统级告警 ——
     * 2026-10-09 事故的教训：那一天三次结算全失败、净值永久缺格，唯一的"通知"
     * 却是一条抢跑的假日报。失败到最后一刻必须是用户能收到的那句真话。
     *
     * @param finalAttemptOfDay 是否当天最后一次尝试（17:10 那一轮与手动补跑传 true）：
     *                          中间态结论（还是没有 bar）到这一刻就是定论，允许发报告。
     *                          16:10 的中间轮传 false —— bar 可能只是还没发布，别抢跑。
     * @return 给任务心跳的一句话摘要（补跑几只、几只仍失败）
     */
    public String retryFailedDailySettlements(boolean finalAttemptOfDay) {
        LocalDate today = CnTime.today();
        int retried = 0;
        List<Strategy> stillFailing = new ArrayList<>();
        for (Strategy strategy : strategyRepository.findByPaperEnabledTrue()) {
            try {
                boolean failedToday = paperTraceService.listForDay(strategy.getId(), today).stream()
                        .anyMatch(trace -> ExecutionContract.SETTLEMENT_DAILY.equals(trace.getSettlementKind())
                                && (ExecutionContract.SKIP_NO_BAR.equals(trace.getSkipReason())
                                        || ExecutionContract.SKIP_DATA_UNAVAILABLE.equals(trace.getSkipReason())));
                if (!failedToday) {
                    continue;
                }
                retried++;
                PaperAccount settled = evaluateStrategy(strategy.getId(), today);
                afterSettlement(strategy, settled, today);
                if (dailySettlementTransient(strategy.getId(), today)) {
                    stillFailing.add(strategy);
                }
                log.info("Settlement retry (final={}) for strategy id={} on {} completed",
                        finalAttemptOfDay, strategy.getId(), today);
            } catch (Exception e) {
                log.error("Settlement retry failed for strategy id={}", strategy.getId(), e);
                stillFailing.add(strategy);   // 抛异常也是"今天没结算成"，同样要被看见
            }
        }
        if (finalAttemptOfDay && !stillFailing.isEmpty()) {
            alertSettlementFailed(stillFailing, today);
        }
        return String.format("补跑 %d 只策略，其中 %d 只当天仍未结算", retried, stillFailing.size());
    }

    /**
     * 终轮结算失败的系统级告警（旁路）：失败只记日志，绝不影响结算本身。
     * 消息走 PAPER_REPORT 类型（用户在"模拟盘复盘"里看系统故障，语义最贴），
     * 幂等键带策略与日期 —— 手动补跑与 17:10 终轮不会推两条。
     */
    private void alertSettlementFailed(List<Strategy> strategies, LocalDate today) {
        if (messageService == null) {
            return;
        }
        for (Strategy strategy : strategies) {
            try {
                if (strategy.getUser() == null) {
                    continue;
                }
                int attempts = paperTraceService.listForDay(strategy.getId(), today).stream()
                        .filter(trace -> ExecutionContract.SETTLEMENT_DAILY.equals(trace.getSettlementKind()))
                        .mapToInt(trace -> trace.getRepeatCount() == null ? 1 : trace.getRepeatCount())
                        .sum();
                String content = String.format(
                        "【结算未完成】%s · %s · %s\n"
                                + "今天的日线结算最终没有完成：到最后一轮重试（17:10）数据源仍未提供当日 K 线，"
                                + "共尝试 %d 次。\n"
                                + "影响：今天的净值曲线会缺一格，委员会未能开会，授权沿用上一次。\n"
                                + "下一交易日 15:30 会自动重试；这通常是数据源当晚发布延迟，"
                                + "持续多天出现才需要排查。",
                        strategy.getName(), strategy.getSymbol(), today, attempts);
                messageService.saveReport(strategy.getUser(), StrategyHealthWatchService.TYPE_STRATEGY_SENTINEL,
                        "paper_settle_failed:" + strategy.getId() + ":" + today,
                        strategy.getSymbol(), content);
            } catch (Exception e) {
                log.warn("结算失败告警推送失败 strategy id={}: {}", strategy.getId(), e.getMessage());
            }
        }
    }

    public void evaluateRealtime() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        for (Strategy strategy : strategies) {
            try {
                // 同 evaluateDaily：Python 调用（最长 60s）不进事务，
                // 落库由各仓储自己的短事务完成（与 startPaper 手动路径同一语义）。
                evaluateRealtimeStrategy(strategy.getId());
            } catch (Exception e) {
                log.error("Realtime paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    /**
     * 每周复盘：先重跑一次**样本外验证**，再发空转期摘要。
     *
     * <h3>为什么验证是每周而不是每天</h3>
     * ① 换票换段的结论不会因为今天多了一根 K 线就变，每天重跑是纯浪费（每次 9 只票的取数）；
     * ② 更重要的是**别让报告变成每天都换一个数字的东西** —— 那样用户学到的教训是
     * "这些数字不用认真看"。周频 + 明确的"上次验证是哪天"，比日频的噪声更有信息量。
     *
     * <p>两步都各自 try/catch：验证失败不该挡掉报告（报告里正会写"这次验证没有可用样本"）。
     */
    public void runWeeklyReview() {
        LocalDate today = CnTime.today();
        for (Strategy strategy : strategyRepository.findByPaperEnabledTrue()) {
            try {
                if (strategyService != null) {
                    strategyService.verifyMatrix(strategy.getUser(), strategy);
                }
            } catch (Exception e) {
                log.error("Weekly verification failed for strategy id={}", strategy.getId(), e);
            }
            // 周反思（P2，经验回流的定性半环）：本周开过委员会的 agent 策略，
            // 把战绩/成交/辩论过程喂给一次 LLM 反思，教训落库供下次决策回读。
            // 失败静默 —— 反思是旁路，绝不影响复盘主链路（与验证各自 try/catch 同一条纪律）。
            if (isAgentMode(strategy)) {
                try {
                    weeklyReflectionQuietly(strategy);
                } catch (Exception e) {
                    log.info("Weekly reflection failed for strategy id={}（不影响复盘）：{}",
                            strategy.getId(), e.toString());
                }
            }
            // 空转期摘要不再推消息（消息中心收窄）：模拟盘页面需要时
            // 通过 PaperReviewReportService.idleWeeklyText 实时取同一份内容。
        }
    }

    /** 周反思教训的 taskType：落库与回读（experience v2）共用这一个键。 */
    private static final String WEEKLY_REFLECTION_TASK_TYPE = "weekly_reflection";
    /** 反思回看的窗口：本周（7 个自然日）的决策与成交。 */
    private static final int REFLECTION_WINDOW_DAYS = 7;

    // ---- 教训退出机制（2026-10-13）：让坏教训自己死掉 ----
    /** 退役判定的最小样本：少于此数的胜率没有统计意义，不退（宁可留着也不小样本撤教训）。 */
    private static final int LESSON_DEMOTE_MIN_TRADES = 4;
    /** 退役的胜率上限（%）：教训出生后的 agent 成交胜率不高于此线 → 自动退役。 */
    private static final double LESSON_DEMOTE_MAX_WIN_RATE = 30.0;

    /**
     * 教训退役判定（纯函数，供测试直接钉住阈值语义）。
     *
     * <p>语义：样本够（≥4 笔已判读）且胜率差（≤30%）才退。两个方向都保守——
     * 小样本不退（3 笔 0% 也可能是运气），胜率不差不退（教训没被证伪就还有资格在场）。
     */
    static boolean shouldRetireLesson(int evaluated, Double winRatePct) {
        if (winRatePct == null || evaluated < LESSON_DEMOTE_MIN_TRADES) {
            return false;
        }
        return winRatePct <= LESSON_DEMOTE_MAX_WIN_RATE;
    }

    /**
     * 教训退出机制：周反思**之前**先体检旧教训。
     *
     * <p>修的断路：教训自出生起随经验包无限期回流委员会，而它的"疗效"从没人复查过——
     * 一条坏教训（例如"跌破 MA5 就跑"在趋势行情里反复打脸）会永远留在提示词里。
     * 现在每周复查一次：教训出生后的 agent 成交已判读 ≥4 笔且胜率 ≤30% → 自动 retired。
     *
     * <p>纪律：① 只用这条策略的 agent 成交（教训只进 agent 路径）；② 小样本不退；
     * ③ 失败静默（不影响周反思本体）；④ 复查发生在反思**之前**——新教训落库前，
     * 旧教训已经完成了这周的生死判定，同一周内不会既退役又复活。
     */
    private void demoteLessonsByOutcomesQuietly(Strategy strategy) {
        if (memoryLessonService == null || decisionQualityService == null
                || strategy.getUser() == null || strategy.getUser().getId() == null) {
            return;
        }
        try {
            Long userId = strategy.getUser().getId();
            List<Map<String, Object>> lessons = memoryLessonService.searchLessons(
                    userId, strategy.getName() + " " + strategy.getSymbol(),
                    WEEKLY_REFLECTION_TASK_TYPE, 5);
            for (Map<String, Object> lesson : lessons) {
                Long id = longOf(lesson.get("id"));
                LocalDateTime recordedAt = parseRecordedAt(lesson.get("recordedAt"));
                if (id == null || recordedAt == null) {
                    continue;
                }
                Map<String, Object> outcome = decisionQualityService
                        .agentOutcomeSince(strategy, recordedAt.toLocalDate());
                int evaluated = intOf(outcome.get("evaluated"));
                Double winRate = doubleOf(outcome.get("winRatePct"));
                if (shouldRetireLesson(evaluated, winRate)) {
                    memoryLessonService.retire(userId, id);
                    log.info("教训自动退役 strategy id={} lesson id={}：出生后 agent 成交"
                                    + " {} 笔已判读、胜率 {}%（≤{}% 阈值）——教训被后续战绩证伪",
                            strategy.getId(), id, evaluated, winRate, LESSON_DEMOTE_MAX_WIN_RATE);
                }
            }
        } catch (Exception e) {
            log.info("教训退役评估失败 strategy id={}（不影响周反思）：{}",
                    strategy.getId(), e.toString());
        }
    }

    private static Long longOf(Object raw) {
        return raw instanceof Number ? ((Number) raw).longValue() : null;
    }

    private static int intOf(Object raw) {
        return raw instanceof Number ? ((Number) raw).intValue() : 0;
    }

    private static Double doubleOf(Object raw) {
        return raw instanceof Number ? ((Number) raw).doubleValue() : null;
    }

    private static LocalDateTime parseRecordedAt(Object raw) {
        if (raw == null) {
            return null;
        }
        try {
            return LocalDateTime.parse(String.valueOf(raw));
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 一次周反思（P2）：本周材料 → Python 一次 LLM 反思 → 教训落 MemoryLesson。
     *
     * <p>门控在先：本周没有委员会记录就不发请求（没有过程就没有教训的原料，
     * 也省一次模型调用）。委员会的过程从痕迹的 snapshot_json 里取（roles 的
     * 发言摘录是委员会自带的审计产物，见 trading_committee.as_audit）。
     */
    private void weeklyReflectionQuietly(Strategy strategy) {
        if (strategy.getUser() == null) {
            return;
        }
        // 教训退出机制：反思之前先体检旧教训（被后续战绩证伪的自动退役，
        // 不再无限期回流进委员会提示词）。放在委员会记录门控之前——
        // 即使本周没开会，上几周的教训也该完成这周的生死判定。
        demoteLessonsByOutcomesQuietly(strategy);
        LocalDate windowStart = CnTime.today().minusDays(REFLECTION_WINDOW_DAYS);
        List<ObjectNode> committees = new ArrayList<>();
        for (PaperTradeTrace trace : paperTraceService.listRecent(strategy.getId(), 40)) {
            if (trace.getTradeDate() == null || trace.getTradeDate().isBefore(windowStart)) {
                continue;
            }
            ObjectNode compact = compactCommitteeOf(trace);
            if (compact != null) {
                committees.add(compact);
            }
        }
        if (committees.isEmpty()) {
            log.debug("Weekly reflection skipped for strategy id={}：本周没有委员会记录", strategy.getId());
            return;
        }

        ObjectNode payload = mapper.createObjectNode();
        ObjectNode meta = payload.putObject("strategy");
        meta.put("name", strategy.getName());
        meta.put("symbol", strategy.getSymbol());
        meta.put("decisionMode", ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
        JsonNode experience = strategyExperienceQuietly(strategy);   // 定量半环做底座
        if (experience != null) {
            payload.set("experience", experience);
        }
        ArrayNode trades = payload.putArray("trades");
        paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId()).stream()
                .filter(trade -> trade.getTradeDate() != null && !trade.getTradeDate().isBefore(windowStart))
                .limit(10)
                .forEach(trade -> {
                    ObjectNode row = trades.addObject();
                    row.put("date", trade.getTradeDate().toString());
                    row.put("side", trade.getSide() == null ? "" : trade.getSide().toLowerCase());
                    row.put("price", trade.getPrice() == null ? null : Money.price(trade.getPrice()));
                    row.put("shares", trade.getShares() == null ? null : Money.price(trade.getShares()));
                });
        payload.set("committee", mapper.valueToTree(
                committees.size() > 3 ? committees.subList(0, 3) : committees));

        JsonNode response;
        try {
            response = strategyClient.weeklyReflection(payload);
        } catch (Exception e) {
            log.info("周反思调用失败 strategy id={}（不影响复盘）：{}", strategy.getId(), e.toString());
            return;
        }
        if (response == null || !response.path("ok").asBoolean(false)) {
            log.info("周反思未产出 strategy id={}：{}", strategy.getId(),
                    response == null ? "无响应" : response.path("warnings").toString());
            return;
        }
        JsonNode lessons = response.path("lessons");
        if (!lessons.isArray() || lessons.isEmpty()) {
            log.info("周反思完成 strategy id={}：本周无可提炼教训（宁缺毋滥是合法结果）", strategy.getId());
            return;
        }
        if (memoryLessonService == null) {
            return;
        }
        MemoryLessonsRequest request = new MemoryLessonsRequest();
        request.setUserId(strategy.getUser().getId());
        request.setSessionKey("weekly-reflection:strategy:" + strategy.getId());
        List<MemoryLessonRequest> items = new ArrayList<>();
        for (JsonNode lesson : lessons) {
            MemoryLessonRequest item = new MemoryLessonRequest();
            item.setTaskType(WEEKLY_REFLECTION_TASK_TYPE);
            item.setSymptom(lesson.path("symptom").asText(""));
            item.setContext(lesson.path("context").asText(""));
            item.setResolution(lesson.path("resolution").asText(""));
            item.setReusableRule(lesson.path("reusable_rule").asText(""));
            item.setConfidence(lesson.path("confidence").isNumber()
                    ? lesson.path("confidence").asDouble() : null);
            item.setProvenance("agent");
            items.add(item);
        }
        request.setLessons(items);
        try {
            memoryLessonService.saveLessons(request);
            log.info("周反思落库 strategy id={}：{} 条教训", strategy.getId(), items.size());
        } catch (Exception e) {
            log.info("周反思教训落库失败 strategy id={}（不影响复盘）：{}",
                    strategy.getId(), e.toString());
        }
    }

    /**
     * 从痕迹的 snapshot_json 里抽出委员会过程的紧凑投影：日期 + 结论 + 角色发言摘录。
     * 兼容 committee 落在顶层或 extra 下两种历史位置；不是委员会痕迹返回 null。
     */
    private ObjectNode compactCommitteeOf(PaperTradeTrace trace) {
        if (!ExecutionContract.DECISION_MODE_AGENT.equals(
                ExecutionContract.normalizeDecisionMode(trace.getDecisionMode()))) {
            return null;
        }
        try {
            JsonNode snapshot = mapper.readTree(
                    trace.getSnapshotJson() == null ? "{}" : trace.getSnapshotJson());
            JsonNode committee = snapshot.path("committee");
            if (!committee.isObject() || committee.isEmpty()) {
                committee = snapshot.path("extra").path("committee");
            }
            if (!committee.isObject() || committee.isEmpty()) {
                return null;   // agent 痕迹但没带辩论记录（如预算/门控跳过）：没有过程可反思
            }
            ObjectNode compact = mapper.createObjectNode();
            compact.put("date", trace.getTradeDate().toString());
            compact.put("decision", trace.getDecision() == null ? "" : trace.getDecision());
            compact.put("signal", trace.getSignal() == null ? "" : trace.getSignal());
            compact.put("skipReason", trace.getSkipReason() == null ? "" : trace.getSkipReason());
            ArrayNode roles = compact.putArray("roles");
            JsonNode rawRoles = committee.path("roles");
            int kept = 0;
            if (rawRoles.isArray()) {
                for (JsonNode role : rawRoles) {
                    ObjectNode row = roles.addObject();
                    row.put("label", role.path("label").asText(role.path("role").asText("")));
                    row.put("said", role.path("excerpt").asText(""));
                    if (++kept >= 8) {
                        break;
                    }
                }
            }
            compact.put("warnings", committee.path("warnings").toString());
            return compact;
        } catch (Exception e) {
            return null;   // 单条痕迹解析失败只跳过它，不拖垮整轮反思
        }
    }

    public PaperAccountResponse getAccount(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("模拟账户不存在"));
        return PaperAccountResponse.from(account);
    }

    public List<PaperTradeResponse> getTrades(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategyId).stream()
                .map(PaperTradeResponse::from)
                .collect(Collectors.toList());
    }

    /**
     * 最近的结算痕迹（含"为什么没成交"）。
     *
     * <p>归属检查走与账户/成交同一条路（`loadStrategy`），痕迹不该成为绕过用户隔离的旁路。
     */
    public List<PaperTradeTraceResponse> getTraces(String username, Long strategyId, int limit) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTraceService.listRecent(strategyId, limit).stream()
                .map(PaperTradeTraceResponse::from)
                .collect(Collectors.toList());
    }

    /**
     * 模拟盘总览：**一次请求回答"在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候"**。
     *
     * <h3>为什么要有这个方法（而不是让前端拼）</h3>
     * 这条功能的失败方式不是算错，而是**用户根本不知道它在跑**：原来要
     * 首页 → 策略库 → 某条策略 → 滚到底 才能看到账户，没有任何地方能一眼看出
     * "哪条在跑、今天动没动、为什么没动"。所以这里把四个问题的答案拼成一份读视图，
     * 页面只负责显示。
     *
     * <p>汇总数字一律来自 {@link PaperEquitySeries#summarize}，"下次评估时间"一律来自
     * {@link PaperSchedule} —— 与详情页、周报共用同一份口径，不在这里另算一套。
     *
     * <p>只有**该用户自己的**策略：归属检查与账户/痕迹走同一条路（{@code listStrategies}），
     * 总览不该成为绕过用户隔离的旁路。
     */
    public List<PaperOverviewResponse> getOverview(String username) {
        User user = getUser(username);
        return strategyRepository.findByUserIdOrderByUpdatedAtDesc(user.getId()).stream()
                .map(this::overviewOf)
                .toList();
    }

    private PaperOverviewResponse overviewOf(Strategy strategy) {
        PaperOverviewResponse item = new PaperOverviewResponse();
        item.setStrategyId(strategy.getId());
        item.setName(strategy.getName());
        item.setSymbol(strategy.getSymbol());
        item.setPaperEnabled(Boolean.TRUE.equals(strategy.getPaperEnabled()));
        item.setDecisionMode(ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
        item.setDecisionModeSince(strategy.getDecisionModeSince());
        item.setConfigUpdatedAt(strategy.getConfigUpdatedAt());

        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
        if (account != null) {
            item.setInitialCapital(Money.amount(account.getInitialCapital()));
            item.setCash(Money.amount(account.getCash()));
            item.setShares(Money.price(account.getShares()));
            item.setAvgCost(Money.price(account.getAvgCost()));
            item.setEquity(Money.amount(account.getEquity()));
            item.setLastPrice(Money.price(account.getLastPrice()));
            item.setLastSignal(account.getLastSignal());
            item.setLastEvalAt(account.getLastEvalAt());
            // 有没有被评估过，用 lastEvalAt 判定（与界面同一依据）：
            // 刚启动、尤其是 agent 模式的账户只有初始资金，不能把那当成"净值就是本金"。
            item.setEvaluated(account.getLastEvalAt() != null);
            if (account.getShares() != null && account.getLastPrice() != null) {
                item.setPositionValue(Money.amount(
                        account.getShares().multiply(account.getLastPrice())));
            }
        }

        List<PaperEquitySnapshot> series =
                paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId());
        if (!series.isEmpty()) {
            item.setSummary(PaperEquitySeries.summarize(series));
        }

        try {
            item.setTraceCount((int) paperTraceService.count(strategy.getId()));
            item.setLastSettlement(paperTraceService.listRecent(strategy.getId(), 1).stream()
                    .findFirst()
                    .map(PaperTradingService::lastSettlementOf)
                    .orElse(null));
        } catch (Exception e) {
            // 旁路读：痕迹读不出来不该让总览整页失败（页面会显示"最近一次结算：读不到"）
            log.warn("读取总览痕迹失败 strategyId={}: {}", strategy.getId(), e.getMessage());
        }

        if (expectationService != null && strategy.getUser() != null) {
            ExpectationService.Expectation expectation = expectationService.latest(
                    strategy.getUser().getId(), strategy.getId());
            if (expectation != null) {
                item.setExpectation(new PaperOverviewResponse.Expectation(
                        expectation.metric(), ExpectationService.label(expectation.metric()),
                        expectation.threshold(), expectation.deadline(), expectation.status(),
                        expectation.outcome(), expectation.isPending(),
                        ExpectationService.describe(expectation)));
            }
        }

        PaperSchedule.NextEvaluation next = PaperSchedule.next(item.getDecisionMode(), PaperSchedule.now());
        item.setNextEvaluation(next);
        item.setNextEvaluationNote(PaperSchedule.describe(next));
        return item;
    }

    /**
     * 最近一次结算的读视图：那句人话直接用痕迹读视图的**完整句子**。
     *
     * <p>不用 {@code sentenceFor(skip_reason)}：它在成交那天只会输出"有成交"，
     * 而这一行要回答的是"今天动没动、动了多少" —— 痕迹的完整句子里已经有
     * 方向、成交价、口径与成交后净值，措辞也只有那一处实现。
     */
    private static PaperOverviewResponse.LastSettlement lastSettlementOf(PaperTradeTrace trace) {
        if (trace == null) {
            return null;
        }
        return new PaperOverviewResponse.LastSettlement(
                trace.getTradeDate(),
                trace.getSettlementKind(),
                trace.getDecision(),
                trace.getSkipReason(),
                trace.getSignal(),
                trace.getDecisionMode(),
                trace.getAgentLlmCalls(),
                trace.getAgentTokens(),
                trace.getTrigger(),
                trace.getCreatedAt(),
                PaperTradeTraceResponse.from(trace).getSummary());
    }

    public List<PaperTradeTraceResponse> getTracesForDay(String username, Long strategyId, LocalDate date) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTraceService.listForDay(strategyId, date).stream()
                .map(PaperTradeTraceResponse::from)
                .collect(Collectors.toList());
    }

    /**
     * 净值曲线（最近 N 个交易日）+ 由同一批点算出的汇总。
     *
     * <p>汇总走 {@link PaperEquitySeries} —— 报告与界面共用同一份算法，
     * 不让"界面一个数、报告另一个数"这种事有机会发生。
     */
    public PaperEquityResponse getEquityCurve(String username, Long strategyId, int days) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        int size = Math.max(1, Math.min(days, 500));
        List<PaperEquitySnapshot> series = new ArrayList<>(
                paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(
                        strategyId, PageRequest.of(0, size)));
        // 仓储按倒序取（要"最近 N 天"），算法要求正序 —— 在这里翻一次，别让算法去猜
        java.util.Collections.reverse(series);
        return new PaperEquityResponse(
                series.stream().map(PaperEquityResponse.Point::from).collect(Collectors.toList()),
                PaperEquitySeries.summarize(series));
    }

    private PaperAccount evaluateStrategy(Long strategyId, LocalDate today) {
        return evaluateStrategy(strategyId, today, ExecutionContract.TRIGGER_CRON);
    }

    /**
     * 切换决策来源后的第一次评估（同步，触发 = manual）。
     *
     * <p>只在切到 agent 且模拟盘在跑时由监听器调用：委员会门控自带 first_decision
     * 触发（从未开过会 ⇒ 开会），这里把"第一次"从下一个 15:30 提前到"现在" ——
     * 换决策者这个动作的意义就是"从现在起按新决策者来"，否则切换后的整个上午
     * 盘中只会反复留下 awaiting_authorization（实测：一天 253 条同一结论）。
     */
    public PaperAccount evaluateOnDecisionModeSwitched(Long strategyId) {
        return evaluateStrategy(strategyId, CnTime.today(), ExecutionContract.TRIGGER_MANUAL);
    }

    /**
     * 日线结算。{@code trigger} 写进每一条痕迹：cron（15:30 定时）还是 manual
     * （启动模拟盘 / 切换决策来源时立刻首会）—— 痕迹必须能回答"这次结算是谁发起的"，
     * 手动触发的首会冒充成 cron 会让"为什么 10:02 有一条日线结算"变成悬案。
     */
    private PaperAccount evaluateStrategy(Long strategyId, LocalDate today, String trigger) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        // 今天是否已经有成交（盘中那条路成交过）。
        //
        // 它**只决定"这一节还能不能下单"**，不再决定"这一节做不做"：
        // 以前这里直接 return null，防重复下单是对的，但连带把当天的净值快照、客观事实、
        // 到期回填、当日报告一起跳过了 —— 于是"今天有成交"这条策略当天在净值曲线上
        // 就是一个缺口，而那恰好是唯一值得看的一天（净值曲线是判断策略有没有变好的主依据）。
        boolean alreadyTradedToday = paperTradeRepository
                .existsByStrategyIdAndTradeDate(strategy.getId(), today);

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }
        // 公司行动先于一切评估：除权日的 HWM/成本不先调整，本节的止损与信号全在错基准上
        account = applyCorporateActionsIfDue(strategy, account);

        JsonNode position = buildPosition(account);
        // 决策源在这里分岔，**执行路径一行不改**（下面还是同一个 applyBarResult）：
        //   rule  → 策略 DSL 出信号
        //   agent → 多角色委员会出决策
        // 两条路都落进同一套封闭枚举与同一个结算管线；区别写在 fingerprint.decision_mode 里，
        // 于是两段曲线永远不会被当成可比（见 ExecutionContract.compareBlockReason）。
        String decisionMode = ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode());
        JsonNode result;
        if (alreadyTradedToday) {
            // 今天已经成交过：这一节只需要**当日收盘价与证据**，不需要任何决策。
            // 所以这里刻意走规则端点（确定性、零成本），而不是问委员会：
            // 委员会一次约 2 万 token，而它的产出（一个信号）在这一节里本来就不允许执行。
            result = strategyClient.evaluateBar(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position);
        } else if (isAgentMode(strategy)) {
            // 信息面证据（P3）+ 经验回流（P4）+ 组合上下文（P5）三包并进委员会证据：
            // 新闻知识、自己的成绩单、自己管着多大集中度。组装失败/为空都不带字段
            // （Python 侧 fail-open）。统一走全量签名——签名组合到此为止。
            JsonNode newsBriefing = committeeBriefingQuietly(strategy.getSymbol());
            JsonNode experience = strategyExperienceQuietly(strategy);
            JsonNode portfolio = portfolioContextQuietly(strategy, account);
            result = strategyClient.agentDecide(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position, lastBriefingDate(strategy.getId()),
                    newsBriefing, experience, portfolio);
        } else {
            result = strategyClient.evaluateBar(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position);
        }
        if (result == null) {
            // 之前这里只打一行日志：那天为什么没结算，除了翻日志没有别的办法查。
            log.warn("{} decision returned null for strategy id={}", decisionMode, strategy.getId());
            return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_DAILY,
                    trigger, today, null, null,
                    ExecutionContract.SKIP_DATA_UNAVAILABLE);
        }
        // 休市/数据未出：Python 端明确标记请求日无 bar 时，禁止用旧 bar 冒充当日成交
        if (result.has("bar_date_missing") && result.get("bar_date_missing").asBoolean(false)) {
            log.warn("Paper settlement skipped for strategy id={} on {}: bar missing (休市/数据未出)",
                    strategy.getId(), today);
            PaperTradeTrace trace = newTrace(strategy, account, ExecutionContract.SETTLEMENT_DAILY,
                    trigger, today, null, result);
            trace.setDecision(ExecutionContract.DECISION_SKIP);
            trace.setSkipReason(skipReasonOf(result, ExecutionContract.SKIP_NO_BAR));
            trace.setSignal(textOr(result.get("signal"), null));
            fillAccountAfter(trace, account);
            paperTraceService.record(trace);
            return account;
        }

        // 委员会带了授权输出就落库（最新一行生效）。失败无害：授权写不进去
        // 只是盘中继续沿用上一次的边界，绝不让它影响本次结算本身。
        if (isAgentMode(strategy)) {
            saveAuthorization(strategy, result);
        }

        PaperAccount settled = applyBarResult(strategy, account, result, today, null,
                ExecutionContract.SETTLEMENT_DAILY, trigger,
                alreadyTradedToday, null);
        recordEquitySnapshot(strategy, settled, result, today);
        recordPaperFacts(strategy, settled, today);
        // 到期就回填预期（复用同一批净值快照，不额外查库）。
        // 旁路：写不进去只是少一次回填，绝不影响结算（与 recordPaperFacts 同一条纪律）。
        if (expectationService != null) {
            expectationService.evaluate(strategy.getUser() == null ? null : strategy.getUser().getId(),
                    strategy.getId(),
                    paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId()),
                    today);
        }
        return settled;
    }

    /**
     * 记一行每日净值快照 —— 只记**日线结算**，且与账户更新在**同一个事务**里。
     *
     * <h3>为什么必须在同一个事务</h3>
     * 净值曲线是时序查询的唯一真相源，客观事实链是它的"记忆语义"副本。
     * 两者若各写各的，"曲线上的净值"与"记忆里的净值"迟早会不一致，
     * 而这种不一致最难查：两边都看起来对。所以：账户、快照、事实在同一笔里落。
     *
     * <h3>为什么结算被跳过时不写</h3>
     * 休市/取数失败那天账户一个字段都没动，补一行"净值不变"是把**没结算**画成**没变化**。
     * 缺口就是缺口：报告会写清楚曲线有多少个点、从哪天到哪天。
     *
     * <p>失败**不吞**：快照属于结算记录本身（不是痕迹那样的旁路），写不进去就该让这笔结算回滚
     * —— 一次写失败意味着那天既没有成交记录也没有净值，重跑一遍即可；
     * 而"吞掉异常、账户动了但曲线缺一格"是**没法事后发现**的错。
     * 所以这里刻意**不**包 try/catch（与 {@code PaperTraceService} 的 fail-open 相反，
     * 因为两者的角色不同：痕迹是旁路证据，净值曲线是主记录）。
     */
    private void recordEquitySnapshot(Strategy strategy, PaperAccount account, JsonNode result,
                                      LocalDate tradeDate) {
        if (strategy == null || account == null || tradeDate == null) {
            return;
        }
        PaperEquitySnapshot snapshot = paperEquitySnapshotRepository
                .findByStrategyIdAndTradeDate(strategy.getId(), tradeDate)
                .orElseGet(() -> PaperEquitySnapshot.builder()
                        .user(strategy.getUser())
                        .strategy(strategy)
                        .tradeDate(tradeDate)
                        .build());
        snapshot.setAccount(account);
        snapshot.setEquity(Money.equity(account.getEquity()));
        snapshot.setCash(Money.amount(account.getCash()));
        snapshot.setShares(Money.price(account.getShares()));
        snapshot.setClosePrice(Money.price(priceOf(result)));
        paperEquitySnapshotRepository.save(snapshot);
    }

    /** 当日收盘价：优先用引擎回传的成交参考价，缺了就用快照里的 close。 */
    private static Double priceOf(JsonNode result) {
        if (result == null) {
            return null;
        }
        Double price = numberOrNull(result.get("price"));
        if (price != null && price > 0) {
            return price;
        }
        JsonNode bar = result.get("snapshot") == null ? null : result.get("snapshot").get("bar");
        return bar == null ? null : numberOrNull(bar.get("close"));
    }

    /**
     * 把当天的模拟盘结算记成**客观事实**（W1）。
     *
     * <h3>为什么只在"每日结算"里记，不在实时结算里记</h3>
     * {@link #evaluateRealtimeStrategy} 由价格刷新事件触发，一天可能跑几十上百次。
     * 在那里记事实的后果不是"多几条"，而是<b>把取代链冲垮</b>：
     * 同一个键上一天出现上百个值，历史链会变得没法读，
     * "净值什么时候真的变了"这个问题的答案就被噪声淹掉了。
     * 事实的粒度应该是"一个有意义的观测点"（收盘结算），不是"每一次内部计算"。
     *
     * <h3>为什么失败不入库也不影响结算</h3>
     * 与 {@link StrategyService} 的回测路径同一条纪律：结算的结果已经算出来了，
     * 记忆写不进去只是少了一份记录，绝不能让它回滚掉真实的成交与持仓。
     */
    private void recordPaperFacts(Strategy strategy, PaperAccount account, LocalDate tradeDate) {
        try {
            if (memoryFactService == null || memoryService == null || strategy == null
                    || account == null || strategy.getUser() == null) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime at = LocalDateTime.of(tradeDate, java.time.LocalTime.of(15, 0));
            List<MemoryFactRequest> facts = new ArrayList<>();
            addFact(facts, subject, ObjectiveFactKeys.PAPER_EQUITY, account.getEquity(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_CASH, account.getCash(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_SHARES, account.getShares(), at);
            // 收益率的口径只该有一处：初始本金是账户自己的字段，不让调用方各算一遍。
            // 用 BigDecimal 除再量化（收益率 4 位）—— 换成 double 除会让"净值没变"的日子
            // 因为浮点误差写出一个非 0 的收益率，取代链上就多出一条假变更。
            BigDecimal initial = account.getInitialCapital();
            BigDecimal equityNow = account.getEquity();
            if (initial != null && initial.signum() != 0 && equityNow != null) {
                BigDecimal returnPct = equityNow.subtract(initial)
                        .multiply(BigDecimal.valueOf(100))
                        .divide(initial, Money.RETURN_SCALE, RoundingMode.HALF_UP);
                addFact(facts, subject, ObjectiveFactKeys.PAPER_RETURN_PCT, returnPct, at);
            }
            addFact(facts, subject, ObjectiveFactKeys.PAPER_LAST_EVAL_AT,
                    at.withNano(0).toString(), at);
            // 由净值序列算出来的两个量：**回撤**与**连续空仓天数**。
            // 它们回答的是"这段时间最难受的一段有多难受"和"已经多久没动了" ——
            // 账户快照只存当前值，所以这两个数只有曲线在才算得出来。
            PaperEquitySeries.Summary series = PaperEquitySeries.summarize(
                    paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId()));
            if (series.maxDrawdownPct() != null) {
                // 直接送 BigDecimal：值格式由 ObjectiveFactKeys.format **一处**决定
                //（≤3 位小数、整数归一成整数；Python 侧同规则）。
                // 在这里先 doubleValue() 会是"两处各决定一次格式"的第一步。
                addFact(facts, subject, ObjectiveFactKeys.PAPER_MAX_DRAWDOWN_PCT,
                        series.maxDrawdownPct(), at);
            }
            if (series.hasData()) {
                addFact(facts, subject, ObjectiveFactKeys.PAPER_FLAT_DAYS, series.flatDays(), at);
            }
            // 里程碑（双层记忆，2026-10-09）：快照类遥测已不可见，用户记忆里
            // 只留"值得记住的事" —— 首笔成交与清仓。判定基于取代链上的上一份持仓。
            String milestone = positionMilestone(
                    memoryFactService.activeFactValues(strategy.getUser().getId(), subject,
                            List.of(ObjectiveFactKeys.PAPER_SHARES))
                            .get(ObjectiveFactKeys.PAPER_SHARES),
                    account.getShares());
            if (milestone != null) {
                MemoryFactRequest story = ObjectiveFactKeys.observationStory(
                        subject, milestone + "（" + tradeDate + "）", at);
                if (story != null) {
                    facts.add(story);
                }
            }
            if (facts.isEmpty()) {
                return;
            }
            memoryFactService.recordObjective(strategy.getUser().getId(),
                    memoryService.currentSessionKey(strategy.getUser().getId()), facts);
        } catch (Exception e) {
            log.warn("模拟盘客观事实入账失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
        }
    }

    /**
     * 持仓状态变化 → 里程碑叙事；无里程碑返回 null。
     * 独立成纯函数：里程碑判定是"什么时候算第一次"的语义，值得单测钉住。
     */
    static String positionMilestone(String previousSharesText, java.math.BigDecimal sharesNow) {
        java.math.BigDecimal previous;
        try {
            previous = previousSharesText == null || previousSharesText.isBlank()
                    ? java.math.BigDecimal.ZERO : new java.math.BigDecimal(previousSharesText.trim());
        } catch (NumberFormatException e) {
            previous = java.math.BigDecimal.ZERO;
        }
        java.math.BigDecimal now = sharesNow == null ? java.math.BigDecimal.ZERO : sharesNow;
        boolean heldBefore = previous.signum() > 0;
        boolean holdsNow = now.signum() > 0;
        if (!heldBefore && holdsNow) {
            return "模拟盘首笔成交，开始持仓";
        }
        if (heldBefore && !holdsNow) {
            return "模拟盘已清仓";
        }
        return null;
    }

    private static void addFact(List<MemoryFactRequest> facts, String subject, String predicate,
                                Object value, LocalDateTime dataAsOf) {
        MemoryFactRequest fact = ObjectiveFactKeys.observation(subject, predicate, value, dataAsOf);
        if (fact != null) {
            facts.add(fact);
        }
    }

    private PaperAccount evaluateRealtimeStrategy(Long strategyId) {
        return evaluateRealtimeStrategy(strategyId, ExecutionContract.TRIGGER_EVENT);
    }

    private PaperAccount evaluateRealtimeStrategy(Long strategyId, String trigger) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }
        // 公司行动先于一切评估：除权日的 HWM/成本不先调整，本节的止损与信号全在错基准上
        account = applyCorporateActionsIfDue(strategy, account);

        // ===== 盘中授权（authorization 模式，2026-10-06；2026-10-08 加止损硬通道）=====
        // 改造前 agent 模式盘中直接短路（2026-09-18 方案 A：一天只剩 15:30 一决），
        // 防的是"委员会说不动、盘中 DSL 照样建仓"的决定冲突。现在决定权以**授权**的
        // 形式前置：委员会开会时划定边界（allow_open/allow_close/max_position_pct），
        // 规则引擎在边界内自动执行——边界内的成交是委员会授权的执行，不是对它的覆盖。
        // 授权是"自上次开会起"的持续状态：门控没开会的日子沿用旧授权，没有空窗
        // （见 PaperAuthorization 的生命周期说明）。
        //
        // 2026-10-08 的教训（国庆后首个交易日大跌）：授权门控**不能挡止损**。
        // 委员会边界管的是"机会"（开不开仓、平不平仓的权衡），止损管的是"生存"——
        // stop_loss_pct / trailing_stop_pct 触发的卖出是账户层的硬风控，无论授权在不在、
        // allow_close 是不是 false，都必须当天盘中执行，否则"委员会还没开会"就等于
        // "没有止损"，大跌日的亏损一路裸奔到 15:30。所以 agent 模式现在也照常做盘中
        // 规则评估（纯规则引擎调用，零 LLM 成本），只是**只有止损类卖出**可以越过门控。
        PaperAuthorization authorization = null;
        boolean intradayAuthorized = true;   // rule 模式：盘中本来就是规则自己的地盘
        if (isAgentMode(strategy)) {
            authorization = paperAuthorizationRepository.findLatest(strategy.getId()).orElse(null);
            intradayAuthorized = authorization != null
                    && Boolean.TRUE.equals(authorization.getIntradayExecution());
        }

        JsonNode position = buildPosition(account);
        JsonNode result = strategyClient.evaluateBarRealtime(
                strategy.getConfigJson(), strategy.getSymbol(), position);
        if (result == null) {
            log.warn("Realtime evaluateBar returned null for strategy id={}", strategy.getId());
            return account;
        }

        String barTime = textOr(result.get("bar_time"), "");
        if (barTime.isBlank()) {
            log.debug("Realtime evaluateBar missing bar_time for strategy id={}", strategy.getId());
            return account;
        }
        // ===== 已完结 bar 门控（2026-10-14）：只对走完的分钟 bar 结算 =====
        // 数据源的分钟 bar 按【结束时刻】标注，且会在盘前预创建当天首根 bar（含 9:25
        // 集合竞价数据）。在 bar 结束时刻之前评估它 = 在不可能成交的时刻、用半程数据
        // 出信号——2026-10-12 09:27:04 的"盘前买入"就是这么发生的（成交价是竞价价
        // 6.79，而 9:30 实际开盘 6.84）；盘中同理，10:42:51 评估"10:43"这根走了一半
        // 的 bar，交叉信号可能在 bar 收尾时消失（重绘）。规则一条：墙钟必须走到
        // bar 标注时刻之后，这根 bar 才算"已完结、可结算"。盘前/午休/收盘后被它
        // 天然挡住，不需要另维护一张时段表；未完结前不占 lastBarTime（不吞掉完结后的结算）。
        LocalDateTime barEnd = parseBarTime(barTime);
        if (barEnd == null) {
            log.warn("Realtime bar_time unparsable ({}), skip settlement for strategy id={}",
                    barTime, strategy.getId());
            return account;
        }
        if (LocalDateTime.now().isBefore(barEnd)) {
            log.debug("Realtime bar {} not finished yet for strategy id={}", barTime, strategy.getId());
            return account;
        }
        if (barTime.equals(account.getLastBarTime())) {
            log.debug("Realtime bar already settled for strategy id={} at {}", strategy.getId(), barTime);
            return account;
        }
        account.setLastBarTime(barTime);

        String pendingSignal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        boolean hardStopSell = isStopLossSell(result, pendingSignal);

        // 授权缺失或被明确关闭：除止损外盘中不执行，但必须留下可见痕迹——
        // "agent 模式为什么盘中没动静"是用户最先问的问题（去重键保证一天一行）。
        // 顺带把账户按最新价标记一下（markToMarket）：组合熔断按当前净值判回撤，
        // agent 账户若一直用昨收估值，大跌日熔断对它永远不触发。
        if (!intradayAuthorized && !hardStopSell) {
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            // 两种"不动"必须分开：从未授权（awaiting_authorization）与
            // 委员会开会后明确关闭盘中执行（agent_intraday_disabled）——
            // 后者是委员会的主动决定，混进"没开会"会让"为什么关掉"永远统计不出来。
            String reason = authorization == null
                    ? ExecutionContract.SKIP_AWAITING_AUTHORIZATION
                    : ExecutionContract.SKIP_AGENT_INTRADAY_DISABLED;
            return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME, trigger,
                    CnTime.today(), barTime, result, reason);
        }

        // ===== 盘中盯盘 agent（agent 模式盘中的唯一决策者，2026-10-15 收权）=====
        // 事件（含 rule_signal —— 规则引擎的信号降级为事件源）叫起轻量盯盘交易员，
        // 在授权边界内下单。**规则不再有自动执行权**：此前"盯盘不参与就回落规则执行"
        // 的设计，让 10-15 09:32 那笔亏损卖出发生在 LLM 降级、无人过目的情况下——
        // agent 模式的盘中动作只能由 agent 的名字签发，模型不在就持仓留痕
        //（冷却/预算/降级各留其因），等下一个事件或 15:30 委员会。
        // 两个例外：止损（hardStopSell）走硬通道不等模型；规则信号本就是 hold 时
        // 照旧走规则路径做重估与留痕（没有交易发生）。
        if (isAgentMode(strategy) && intradayAuthorized && !hardStopSell && intradayWatchEnabled) {
            PaperAccount watched = runIntradayWatch(strategy, account, result, authorization,
                    barTime, trigger);
            if (watched != null) {
                return watched;
            }
            boolean signalIsAction = ExecutionContract.DECISION_BUY.equals(pendingSignal)
                    || ExecutionContract.DECISION_SELL.equals(pendingSignal);
            if (signalIsAction) {
                // 走到这里说明盯盘内部已按各 null 路径留痕（冷却/预算/降级/异常）——
                // 这里不再重复落痕，直接收权返回：规则想动，但没有 agent 签名，不动。
                return account;
            }
        }

        // 组合熔断：该用户全部模拟盘当日总回撤超过阈值时，**新开仓**一律挡下
        //（平仓不挡——熔断是把风险降下来，不是把出口锁死；止损卖出同属"降风险"，
        // 自然也不挡）。规则与 agent 一视同仁：组合风险不关心这一单是谁决定的。
        // 检查内部 fail-open（组合读挂了不停交易）。
        if ("buy".equals(pendingSignal) && portfolioService != null
                && strategy.getUser() != null
                && portfolioService.haltsNewOpens(strategy.getUser().getId())) {
            return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME, trigger,
                    CnTime.today(), barTime, result, ExecutionContract.SKIP_PORTFOLIO_HALTED);
        }

        // 授权边界检查：信号方向越界就不执行。落 trace 的原因是
        // outside_authorization 而不是 rule_not_met——"被边界挡下"与"市场没给机会"
        // 必须分得开，委员会复盘时才能看到自己的边界挡了多少单。
        // 唯一的例外是止损卖出（hardStopSell）：allow_close=false 挡不住它，
        // 见上面"止损硬通道"的说明。
        if (authorization != null) {
            boolean openBlocked = "buy".equals(pendingSignal) && !Boolean.TRUE.equals(authorization.getAllowOpen());
            boolean closeBlocked = "sell".equals(pendingSignal)
                    && !Boolean.TRUE.equals(authorization.getAllowClose()) && !hardStopSell;
            if (openBlocked || closeBlocked) {
                markToMarket(strategy, account, result);
                markFingerprintDecisionMode(result);
                return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME, trigger,
                        CnTime.today(), barTime, result, ExecutionContract.SKIP_OUTSIDE_AUTHORIZATION);
            }
        }
        // 当日往返护栏（可配置，默认开）：盘中授权通道一天只许动一次手。
        // 实测踩过：10:08 规则卖出 → 10:12 规则买回（2/10 日线在分钟级震荡里互搏）→
        // 10:20 再触发卖出被 T+1 锁死，全天两笔双边费用、被动持有到收盘。
        // 它只拦 agent 模式的盘中**买入**；规则模式多笔、止损卖出不受影响。
        // 日内多笔（做T/分批）的合法部署用 paper.intraday.roundtrip-guard-enabled=false 关掉。
        if (roundtripGuardEnabled && isAgentMode(strategy) && "buy".equals(pendingSignal)
                && paperTradeRepository.existsByStrategyIdAndTradeDate(strategy.getId(), CnTime.today())) {
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            PaperAccount blocked = traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME, trigger,
                    CnTime.today(), barTime, result, ExecutionContract.SKIP_ALREADY_TRADED_TODAY);
            return blocked;
        }
        if (isAgentMode(strategy)) {
            // 止损通道与授权通道分开标注：委员会复盘时要能分清"边界内的执行"
            // 与"越过边界的硬止损"——后者是它下次开会该重新评估边界的信号。
            markAuthorizedExecution(result, hardStopSell
                    ? "intraday_hard_stop" : "intraday_authorized_execution");
        }
        // 刻意**不**在这里写客观事实：本方法由每次价格刷新触发，
        // 在这里记会把取代链冲成一天上百个值（见 recordPaperFacts 的说明）。
        // alreadyTradedToday=false：盘中这条**就是**下单的那条路，
        // 它自己要跑的就是"能不能成交"的判断（重复成交由 dedupe 键与 T+1 守卫挡）。
        return applyBarResult(strategy, account, result, CnTime.today(), barTime,
                ExecutionContract.SETTLEMENT_REALTIME, trigger, false,
                authorization == null ? null : authorization.getMaxPositionPct());
    }

    /**
     * 盯盘交易员的一次完整回合：过滤冷却 → 查预算 → 调 Python 盯盘端点 → 按动作执行。
     *
     * <p>返回值语义（调用方据此分流）：
     * <ul>
     *   <li>非 null：盯盘已给出**结论**（成交 / hold 留痕 / flag 标记），本节到此为止；</li>
     *   <li>null：盯盘**不参与**本节（无事件 / 全在冷却 / 预算尽已留痕 / LLM 降级），
     *       调用方落回"规则 + 授权"的执行路径。</li>
     * </ul>
     */
    /** news_shock 的事件级冷却：同一条新闻 4 小时内只叫醒一次模型（类型冷却 15 分钟挡不住它）。 */
    private static final long NEWS_SHOCK_COOLDOWN_MS = 4 * 60 * 60 * 1000L;
    private final Map<String, Long> newsShockLastAlerted = new ConcurrentHashMap<>();

    private PaperAccount runIntradayWatch(Strategy strategy, PaperAccount account, JsonNode result,
                                          PaperAuthorization authorization, String barTime,
                                          String trigger) {
        JsonNode events = result.get("events");
        boolean hasMarketEvents = events != null && events.isArray() && !events.isEmpty();
        // news_shock（P3）：无盘中事件时，high impact 新闻也能单独叫醒盯盘；
        // 有盘中事件时作为附加上下文一并给（同一根 bar 上"急跌 + 利空新闻"是强信号）。
        JsonNode newsAlert = latestShockQuietly(strategy);
        if (!hasMarketEvents) {
            if (newsAlert == null) {
                return null;   // 无事件无新闻：零成本，规则路径照旧
            }
            // 合成与 evaluate-bar 同形状的事件数组（filterCooledEvents 按 type 读）
            ObjectNode withNews = mapper.createObjectNode();
            ArrayNode synth = withNews.putArray("events");
            synth.addObject().put("type", "news_shock");
            ((ObjectNode) result).set("events", synth);
            events = synth;
        }
        List<String> freshTypes = filterCooledEvents(strategy.getId(), events);
        if (freshTypes.isEmpty()) {
            // 全在冷却窗口内：同一件事刚问过模型，不叫第二次——留痕说明"门控决定不叫"
            //（agent 模式盘中不再回落规则自动执行，2026-10-15）
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                    ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                    ExecutionContract.SKIP_AGENT_NO_NEW_INFORMATION);
            return null;
        }
        if (watchCallsUsed(strategy.getId()) >= intradayWatchDailyBudget) {
            // 预算尽：留一行痕（dedupe 保证一天一行）说明"盯盘今天下班了"。
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            markWatchAudit(result, freshTypes, "budget_exceeded", null);
            traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                    ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                    ExecutionContract.SKIP_INTRADAY_WATCH_BUDGET_EXCEEDED);
            return null;
        }

        JsonNode watch;
        try {
            // 盘中语境（2026-10-14）：当日已成交 + 上次全委会结论，随全部调用带上
            //（experience/newsAlert 可为 null，todayContext 组装失败时是空节点——都不挡调用）
            JsonNode experience = strategyExperienceQuietly(strategy);
            watch = strategyClient.intradayWatch(strategy.getConfigJson(), strategy.getSymbol(),
                    events, buildPosition(account), authorizationNode(authorization),
                    newsAlert, experience, todayContextNode(strategy));
        } catch (Exception e) {
            log.warn("Intraday watch call failed for strategy id={}: {}", strategy.getId(), e.getMessage());
            // 问没问成：留痕说明"模型不可用，本节不动"——不再回落规则自动执行（2026-10-15）：
            // agent 模式的盘中动作只能由 agent 的名字签发，宁可不动也不错剁。
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                    ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                    ExecutionContract.SKIP_AGENT_LLM_UNAVAILABLE);
            alertLlmDegradedQuietly(strategy, e.getMessage());
            return null;
        }
        if (watch == null || watch.path("degraded").asBoolean(false)) {
            log.info("Intraday watch degraded for strategy id={} on {} — holding, no rule fallback",
                    strategy.getId(), barTime);
            markToMarket(strategy, account, result);
            markFingerprintDecisionMode(result);
            traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                    ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                    ExecutionContract.SKIP_AGENT_LLM_UNAVAILABLE);
            alertLlmDegradedQuietly(strategy, null);
            return null;   // LLM 降级 → 持仓留痕，等下一个事件或 15:30 委员会
        }
        watchCallCount.merge(strategy.getId() + ":" + CnTime.today(), 1, Integer::sum);

        String action = textOr(watch.get("action"), "hold").toLowerCase(Locale.ROOT);
        BigDecimal size = Money.ratePct(numberOr(watch.get("size_fraction"), null));
        boolean actionableSize = size != null && size.signum() > 0 && size.compareTo(BigDecimal.ONE) <= 0;
        markToMarket(strategy, account, result);
        markFingerprintDecisionMode(result);
        markWatchAudit(result, freshTypes, action, watch);

        // "buy 但没给有效仓位"是自相矛盾的输出：按 hold 处理（与全委会的 decision=skip 纪律同源）
        if ((ExecutionContract.DECISION_BUY.equals(action) || "partial_sell".equals(action))
                && !actionableSize) {
            log.warn("Intraday watch action={} but no usable size_fraction for strategy id={} — hold",
                    action, strategy.getId());
            action = "hold";
        }

        switch (action) {
            case "hold" -> {
                // 模型看过了、决定不动（含否决规则信号）—— 这正是要审计的分歧
                return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                        ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                        ExecutionContract.SKIP_WATCH_HOLD);
            }
            case "flag" -> {
                notifyWatchFlag(strategy, result, freshTypes, watch);
                return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                        ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                        ExecutionContract.SKIP_WATCH_HOLD);
            }
            case "buy", "sell", "partial_sell" -> {
                // 方向边界：越界的建议不执行（留痕 + 推送），委员会复盘时能看到
                // 自己的边界挡了多少次盯盘建议
                boolean openBlocked = "buy".equals(action)
                        && authorization != null && !Boolean.TRUE.equals(authorization.getAllowOpen());
                boolean closeBlocked = !("buy".equals(action))
                        && authorization != null && !Boolean.TRUE.equals(authorization.getAllowClose());
                if (openBlocked || closeBlocked) {
                    notifyWatchFlag(strategy, result, freshTypes, watch);
                    return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                            ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                            ExecutionContract.SKIP_OUTSIDE_AUTHORIZATION);
                }
                // 组合熔断与规则路径同一道闸：盯盘的开仓建议不豁免
                if ("buy".equals(action) && portfolioService != null && strategy.getUser() != null
                        && portfolioService.haltsNewOpens(strategy.getUser().getId())) {
                    return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                            ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                            ExecutionContract.SKIP_PORTFOLIO_HALTED);
                }
                // 合成执行响应：signal/size_fraction 来自盯盘，价格/指纹/快照沿用同一根 bar。
                // 下面的 applyBarResult 会照旧走完整执行纪律（整手/佣金/T+1/三层仓位夹取）。
                ObjectNode synth = mapper.createObjectNode();
                synth.put("signal", "buy".equals(action) ? "buy" : "sell");
                if (result.has("price")) {
                    synth.set("price", result.get("price"));
                }
                synth.put("size_fraction",
                        "partial_sell".equals(action) ? size.doubleValue()
                                : ("buy".equals(action) ? size.doubleValue() : 1.0));
                // matched_conditions 进痕迹的 reason 列：盯盘动作 + 触发它的事件一眼可读
                ArrayNode conditions = synth.putArray("matched_conditions");
                conditions.add("watch_" + action);
                freshTypes.forEach(conditions::add);
                if (result.has("fingerprint")) {
                    synth.set("fingerprint", result.get("fingerprint"));
                }
                if (result.has("snapshot")) {
                    synth.set("snapshot", result.get("snapshot"));
                }
                return applyBarResult(strategy, account, synth, CnTime.today(), barTime,
                        ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.TRIGGER_WATCH,
                        false, authorization == null ? null : authorization.getMaxPositionPct());
            }
            default -> {
                log.warn("Unknown watch action '{}' for strategy id={} — hold", action, strategy.getId());
                return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_REALTIME,
                        ExecutionContract.TRIGGER_WATCH, CnTime.today(), barTime, result,
                        ExecutionContract.SKIP_WATCH_HOLD);
            }
        }
    }


    /** 委员会的信息面证据包（P3）：组装失败/为空都返回 null（不带字段，fail-open）。 */
    private JsonNode committeeBriefingQuietly(String symbol) {
        if (newsBriefingService == null) {
            return null;
        }
        try {
            JsonNode briefing = newsBriefingService.committeeBriefing(symbol);
            return NewsBriefingService.isEmptyBriefing(briefing) ? null : briefing;
        } catch (Exception e) {
            log.info("委员会信息面证据组装失败 symbol={}（不影响决策）：{}", symbol, e.toString());
            return null;
        }
    }

    /**
     * 策略的经验包（P4 经验回流 + P2 定性半环）：定量成绩单（胜率/对齐收益）为主，
     * 有周反思教训时附带最近 2 条（symptom + 可执行规则）—— 委员会既能看见
     * "错了多少"，也能看见"为什么错、下次怎么做"。无成交/组装失败都返回 null
     * （fail-open）；教训查询失败只少一块，绝不让经验包整体失败。
     */
    private JsonNode strategyExperienceQuietly(Strategy strategy) {
        if (decisionQualityService == null) {
            return null;
        }
        try {
            Map<String, Object> experience = decisionQualityService.strategyExperience(strategy, 6);
            if (experience == null || experience.isEmpty()) {
                return null;
            }
            ObjectNode node = mapper.valueToTree(experience);
            attachRecentLessons(strategy, node);
            return node;
        } catch (Exception e) {
            log.info("策略经验包组装失败 strategy id={}（不影响决策）：{}",
                    strategy.getId(), e.toString());
            return null;
        }
    }

    /** 经验包 v2 的定性半环：最近 2 条周反思教训（active 优先，打分交给教训服务）。 */
    private void attachRecentLessons(Strategy strategy, ObjectNode experience) {
        if (memoryLessonService == null || strategy.getUser() == null) {
            return;
        }
        try {
            // 记忆分区（2026-10-15）：只读本策略 scope 的教训。此前只按 taskType 过滤，
            // 多策略并跑时 A 策略的委员会会把 B 策略的教训也读进去 —— 用户点名要治的"记忆错乱"。
            List<Map<String, Object>> lessons = memoryLessonService.searchLessons(
                    strategy.getUser().getId(),
                    strategy.getName() + " " + strategy.getSymbol(),
                    WEEKLY_REFLECTION_TASK_TYPE,
                    com.happyericsix.stocktracker.entity.MemoryLesson.strategyScope(strategy.getId()), 2);
            if (lessons == null || lessons.isEmpty()) {
                return;
            }
            ArrayNode rows = experience.putArray("lessons");
            for (Map<String, Object> lesson : lessons) {
                ObjectNode row = rows.addObject();
                row.put("symptom", String.valueOf(lesson.getOrDefault("symptom", "")));
                row.put("reusableRule", String.valueOf(lesson.getOrDefault("reusableRule", "")));
            }
            experience.put("lessonsNote",
                    "以下是过去周反思提炼的教训（历史经验的定性半环，供参考不是禁令）。");
        } catch (Exception e) {
            log.debug("教训回读失败 strategy id={}（经验包照常，只是少了定性半环）：{}",
                    strategy.getId(), e.toString());
        }
    }

    /**
     * 组合上下文（P5，参考 TradingAgents 的 PortfolioContext）：委员会不该只看见
     * 这一条策略 —— 同一个行情，"满仓单票"和"三成仓位还有两条策略在跑"该给不同答案。
     * 内容：本策略现金/持仓占比 + 该用户全部在跑策略的敞口清单。纯读库、零 LLM；
     * 组装失败返回 null（fail-open）。
     */
    private JsonNode portfolioContextQuietly(Strategy strategy, PaperAccount account) {
        if (strategy.getUser() == null) {
            return null;
        }
        try {
            ObjectNode node = mapper.createObjectNode();
            node.put("note", "账户与组合上下文：你管的不只这一条策略——评估集中度与仓位比例时以此为准。");

            ObjectNode self = node.putObject("thisStrategy");
            self.put("cash", Money.amount(orZero(account.getCash())));
            if (account.getShares() != null && account.getShares().signum() > 0
                    && account.getLastPrice() != null && account.getEquity() != null
                    && account.getEquity().signum() > 0) {
                java.math.BigDecimal positionValue = account.getShares().multiply(account.getLastPrice());
                self.put("shares", Money.price(account.getShares()));
                self.put("positionValue", Money.amount(positionValue));
                self.put("positionSharePct", positionValue
                        .divide(account.getEquity(), 4, java.math.RoundingMode.HALF_UP)
                        .multiply(java.math.BigDecimal.valueOf(100))
                        .setScale(1, java.math.RoundingMode.HALF_UP));
            } else {
                self.put("shares", 0);
                self.put("positionSharePct", 0);
            }

            ObjectNode portfolio = node.putObject("userPortfolio");
            java.util.List<Strategy> running = strategyRepository
                    .findByUserIdOrderByUpdatedAtDesc(strategy.getUser().getId()).stream()
                    .filter(s -> Boolean.TRUE.equals(s.getPaperEnabled()))
                    .toList();
            portfolio.put("strategiesRunning", running.size());
            ArrayNode positions = portfolio.putArray("openPositions");
            java.math.BigDecimal totalPositionValue = java.math.BigDecimal.ZERO;
            java.math.BigDecimal totalEquity = java.math.BigDecimal.ZERO;
            for (Strategy s : running) {
                PaperAccount a = paperAccountRepository.findByStrategyId(s.getId()).orElse(null);
                if (a == null || a.getShares() == null || a.getShares().signum() <= 0
                        || a.getLastPrice() == null) {
                    continue;
                }
                java.math.BigDecimal pv = a.getShares().multiply(a.getLastPrice());
                totalPositionValue = totalPositionValue.add(pv);
                if (a.getEquity() != null) {
                    totalEquity = totalEquity.add(a.getEquity());
                }
                ObjectNode row = positions.addObject();
                row.put("name", s.getName());
                row.put("symbol", s.getSymbol());
                row.put("positionValue", Money.amount(pv));
            }
            portfolio.put("totalPositionValue", Money.amount(totalPositionValue));
            if (totalEquity.signum() > 0) {
                portfolio.put("allStrategiesPositionPct", totalPositionValue
                        .divide(totalEquity, 4, java.math.RoundingMode.HALF_UP)
                        .multiply(java.math.BigDecimal.valueOf(100))
                        .setScale(1, java.math.RoundingMode.HALF_UP));
            }
            return node;
        } catch (Exception e) {
            log.info("组合上下文组装失败 strategy id={}（不影响决策）：{}",
                    strategy.getId(), e.toString());
            return null;
        }
    }

    /**
     * 盯盘的 news_shock（P3）：该标的近 24h 的 high impact 已判方向新闻，
     * 带 4 小时事件级冷却（同一条新闻反复触发只叫醒一次）。
     * 检测失败/无新闻/服务未装配都返回 null（零成本）。
     */
    private JsonNode latestShockQuietly(Strategy strategy) {
        if (newsBriefingService == null) {
            return null;
        }
        try {
            JsonNode shock = newsBriefingService.latestShock(strategy.getSymbol(), 24);
            if (shock == null) {
                return null;
            }
            String shockKey = strategy.getId() + ":news:" + shock.path("id").asLong();
            long now = System.currentTimeMillis();
            Long last = newsShockLastAlerted.get(shockKey);
            if (last != null && now - last < NEWS_SHOCK_COOLDOWN_MS) {
                return null;
            }
            newsShockLastAlerted.put(shockKey, now);
            return shock;
        } catch (Exception e) {
            log.debug("news_shock 检测失败 symbol={}（忽略）：{}", strategy.getSymbol(), e.toString());
            return null;
        }
    }

    /** 事件冷却：同一事件类型在窗口内只叫醒模型一次（一次急涨往往连着几根 bar 都"在触发"）。 */
    private List<String> filterCooledEvents(Long strategyId, JsonNode events) {        List<String> fresh = new ArrayList<>();
        long now = System.currentTimeMillis();
        for (JsonNode event : events) {
            String type = event.path("type").asText("");
            if (type.isBlank()) {
                continue;
            }
            String key = strategyId + ":" + type;
            Long last = watchEventLastFired.get(key);
            if (last != null && now - last < WATCH_EVENT_COOLDOWN_MS) {
                continue;
            }
            watchEventLastFired.put(key, now);
            fresh.add(type);
        }
        return fresh;
    }

    private int watchCallsUsed(Long strategyId) {
        return watchCallCount.getOrDefault(strategyId + ":" + CnTime.today(), 0);
    }

    /** 授权边界给模型看的形状（Python 侧 authorization 字段，口径以 PaperAuthorization 为准）。 */
    private ObjectNode authorizationNode(PaperAuthorization authorization) {
        ObjectNode node = mapper.createObjectNode();
        if (authorization == null) {
            node.putNull("intraday_execution");
            return node;
        }
        node.put("intraday_execution", Boolean.TRUE.equals(authorization.getIntradayExecution()));
        node.put("allow_open", Boolean.TRUE.equals(authorization.getAllowOpen()));
        node.put("allow_close", Boolean.TRUE.equals(authorization.getAllowClose()));
        node.put("max_position_pct", authorization.getMaxPositionPct() == null
                ? 1.0 : authorization.getMaxPositionPct().doubleValue());
        if (authorization.getNotes() != null) {
            node.put("notes", authorization.getNotes());
        }
        return node;
    }

    /** 把盯盘的判断写进响应快照的 extra：痕迹因此能回答"盯盘看到了什么、说了什么"。 */
    private static void markWatchAudit(JsonNode result, List<String> eventTypes, String action,
                                       JsonNode watch) {
        try {
            JsonNode snapshot = result.get("snapshot");
            if (!(snapshot instanceof ObjectNode snap)) {
                return;
            }
            JsonNode extra = snap.get("extra");
            if (!(extra instanceof ObjectNode ex)) {
                return;
            }
            ObjectNode audit = ex.putObject("intraday_watch");
            audit.put("events", String.join(",", eventTypes));
            audit.put("action", action);
            if (watch != null) {
                audit.put("reason", watch.path("reason").asText(""));
                audit.put("confidence", watch.path("confidence").asDouble(0.0));
                audit.put("tokens", watch.path("watch_tokens").asInt(0));
                if (watch.has("warnings")) {
                    audit.set("warnings", watch.get("warnings"));
                }
            }
        } catch (Exception e) {
            // 旁路：审计标记失败不该影响盯盘本身
        }
    }

    /** flag（盯盘标记给 15:30 委员会）与越界建议的推送旁路：失败只记日志。 */
    private void notifyWatchFlag(Strategy strategy, JsonNode result, List<String> eventTypes,
                                 JsonNode watch) {
        if (pushService == null || strategy.getUser() == null) {
            return;
        }
        try {
            String reason = watch == null ? "" : watch.path("reason").asText("");
            pushService.push(strategy.getUser().getUsername(),
                    "盯盘标记：" + strategy.getName() + " " + strategy.getSymbol(),
                    String.format("事件 %s；盯盘判断：%s（不下单，已标记给 15:30 委员会）",
                            String.join("/", eventTypes), reason));
        } catch (Exception e) {
            log.debug("watch flag push failed for strategy {}: {}", strategy.getId(), e.getMessage());
        }
    }

    /**
     * 这个信号是不是**止损类卖出**（硬风控，不受授权门控约束）。
     *
     * <p>只认引擎在 {@code matched_conditions} 里点名的那两类：{@code stop_loss_pct}
     * （相对成本亏到线）与 {@code trailing_stop_pct}（从最高水位回撤到线）。
     * {@code take_profit_pct} 刻意不算——止盈是收益端的取舍，可以交给委员会权衡；
     * 止损是生存底线，不能等。
     */
    private static boolean isStopLossSell(JsonNode result, String signal) {
        if (!"sell".equals(signal)) {
            return false;
        }
        JsonNode matched = result == null ? null : result.get("matched_conditions");
        if (matched == null || !matched.isArray()) {
            return false;
        }
        for (JsonNode condition : matched) {
            String type = condition.asText("");
            if ("stop_loss_pct".equals(type) || "trailing_stop_pct".equals(type)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 盘中标记市值：agent 模式**不交易**的那些 tick 也要按最新价刷新估值。
     *
     * <p>为什么必须做：组合熔断（PortfolioService）按账户当前净值判定单日回撤，
     * agent 账户若盘中一直沿用上一次结算的估值，大跌日熔断对它永远不触发——
     * 等 15:30 才发现回撤超限，熔断已经没有意义。这也是对原设计"agent 模式盘中
     * 不刷新净值"的一次**有意放宽**：净值**曲线**不受影响（快照仍只在日线结算写，
     * 一天一格），变的只是账户的当前读数与最高水位。
     *
     * <p>刻意不写 lastSignal / lastEvalAt / 客观事实：那三个字段说的是"决策"，
     * 而这里只是估值；把它们刷了会把"委员会还没决策"装成"决策过但没动"。
     */
    private PaperAccount markToMarket(Strategy strategy, PaperAccount account, JsonNode result) {
        try {
            BigDecimal price = Money.price(priceOf(result));
            if (price == null || price.signum() <= 0 || !isHolding(account)) {
                return account;   // 空仓账户的净值就是现金，没有可标记的
            }
            BigDecimal equityBefore = orZero(account.getEquity());
            BigDecimal priceBefore = orZero(account.getLastPrice());
            updateEquityAndHighWatermark(account, price);
            account.setLastPrice(price);
            if (account.getEquity().compareTo(equityBefore) != 0 || price.compareTo(priceBefore) != 0) {
                return paperAccountRepository.save(account);
            }
            return account;
        } catch (Exception e) {
            // 旁路：估值刷不进去只是让熔断晚一步看见，绝不影响主流程
            log.debug("mark-to-market failed for strategy id={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
            return account;
        }
    }

    /**
     * 这条策略现在由谁做决定。
     *
     * <p>只留一个判定入口：日线结算、盘中检查、仓位上限三处都要问同一个问题，
     * 各写一遍 {@code normalize + equals} 迟早会出现"这里算了那里没算"，
     * 而那种偏差的表现是"某条路径偷偷用了另一个决策者"。
     */
    private static boolean isAgentMode(Strategy strategy) {
        return strategy != null && ExecutionContract.DECISION_MODE_AGENT.equals(
                ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
    }

    /** 最近一次真的开了会的日子（给门控用）。读不到返回 null（Python 侧按"从没开过"处理）。 */
    private String lastBriefingDate(Long strategyId) {
        try {
            return paperTraceService == null ? null : paperTraceService.lastBriefingDate(strategyId);
        } catch (Exception e) {
            log.debug("读取上次开会日期失败 strategyId={}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    /**
     * 把委员会输出里的 authorization 落库（只追加，最新一行生效）。
     *
     * <p>没有 authorization 字段就什么都不做——授权只在**有新授权**时更新：
     * 门控没开会、预算耗尽、LLM 不可用，这些日子旧授权继续有效（没有空窗）。
     * 这也是"委员会明确关掉盘中执行"（intraday_execution=false）能生效的原因：
     * 关闭同样是一次带 authorization 的输出。
     */
    private void saveAuthorization(Strategy strategy, JsonNode result) {
        try {
            JsonNode auth = result == null ? null : result.get("authorization");
            if (auth == null || !auth.isObject()) {
                return;
            }
            boolean intraday = auth.path("intraday_execution").asBoolean(false);
            boolean allowOpen = auth.has("allow_open")
                    ? auth.path("allow_open").asBoolean(intraday) : intraday;
            boolean allowClose = auth.has("allow_close")
                    ? auth.path("allow_close").asBoolean(true) : true;
            double rawPct = auth.path("max_position_pct").asDouble(1.0);
            double clamped = Math.min(1.0, Math.max(0.0, Double.isNaN(rawPct) ? 1.0 : rawPct));
            paperAuthorizationRepository.save(PaperAuthorization.builder()
                    .user(strategy.getUser())
                    .strategy(strategy)
                    .intradayExecution(intraday)
                    .allowOpen(allowOpen)
                    .allowClose(allowClose)
                    .maxPositionPct(Money.quantize(clamped, Money.RETURN_SCALE))
                    .notes(clipText(textOr(auth.get("notes"), null), 500))
                    .build());
            log.info("Saved intraday authorization for strategy id={}: intraday={} open={} close={} maxPct={}",
                    strategy.getId(), intraday, allowOpen, allowClose, clamped);
        } catch (Exception e) {
            log.warn("Authorization save failed for strategy id={}: {}",
                    strategy.getId(), e.getMessage());
        }
    }

    /**
     * 把"授权内的规则执行"标记进响应：fingerprint 的 decision_mode 改回 agent
     * （决定权持有者是委员会的授权，曲线因此保持 agent 段连续可比，trigger=event
     * 已经能区分这是盘中路径），snapshot.extra 记一笔供审计辨认"这单是边界内自动执行的"
     * （{@code intraday_authorized_execution}）还是"越过边界的硬止损"
     * （{@code intraday_hard_stop}）。
     */
    private void markAuthorizedExecution(JsonNode result, String extraKey) {
        try {
            markFingerprintDecisionMode(result);
            JsonNode snapshot = result.get("snapshot");
            JsonNode extra = snapshot == null ? null : snapshot.get("extra");
            if (extra instanceof tools.jackson.databind.node.ObjectNode ex) {
                ex.put(extraKey, true);
            }
        } catch (Exception e) {
            log.debug("Failed to mark authorized execution: {}", e.getMessage());
        }
    }

    /**
     * 只把响应指纹的 decision_mode 改成 agent：agent 模式策略的**被挡下的**盘中 tick
     * 也要正确落"决策者=多角色委员会"——规则引擎的响应指纹默认写着 rule，
     * 不改的话那一行痕迹会把 agent 策略记成规则策略。
     */
    private static void markFingerprintDecisionMode(JsonNode result) {
        try {
            JsonNode fingerprint = result.get("fingerprint");
            if (fingerprint instanceof tools.jackson.databind.node.ObjectNode fp) {
                fp.put("decision_mode", ExecutionContract.DECISION_MODE_AGENT);
            }
        } catch (Exception e) {
            log.debug("Failed to mark fingerprint decision mode: {}", e.getMessage());
        }
    }

    private static String clipText(String text, int limit) {
        if (text == null) {
            return null;
        }
        return text.length() <= limit ? text : text.substring(0, limit);
    }

    /**
     * 只写痕迹、不改账户（用于"这一轮什么都没做"的路径）。
     *
     * <p>把它单独拿出来，是因为这类分支以前全都是 `return account;` ——
     * 对调用方而言与"结算了但没动"完全一样，而它们的原因可能天差地别
     * （取数不可用 / 休市 / 涨停挡单 / 现金不足一手）。痕迹是唯一能把它们分开的地方。
     */
    private PaperAccount traceOnly(Strategy strategy, PaperAccount account, String settlementKind,
                                   String trigger, LocalDate tradeDate, String barTime,
                                   JsonNode result, String skipReason) {
        PaperTradeTrace trace = newTrace(strategy, account, settlementKind, trigger, tradeDate,
                barTime, result);
        trace.setDecision(ExecutionContract.DECISION_SKIP);
        trace.setSkipReason(ExecutionContract.normalizeSkipReason(skipReason));
        fillAccountAfter(trace, account);
        paperTraceService.record(trace);
        return account;
    }

    /**
     * 按一根 bar 结算。
     *
     * @param alreadyTradedToday 今天已经成交过（盘中那条路成交的）→ **这一节不下单，只重估**。
     *                           它把"同一天不能下两次单"这条保证从"整段跳过"收窄成"禁止下单"：
     *                           账户、净值快照、客观事实、到期回填、当日报告统统照旧。
     */
    private PaperAccount applyBarResult(Strategy strategy, PaperAccount account,
                                        JsonNode result, LocalDate tradeDate, String barTime,
                                        String settlementKind, String trigger,
                                        boolean alreadyTradedToday, BigDecimal sizeCap) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        JsonNode risk = config.get("risk");
        // 费率一律走 BigDecimal：它们是"乘在钱上的数"，用 double 相乘会把二进制误差
        // 带进成交额，而净值恒等式要逐笔对上账（见 Money）。
        //
        // 费率出处（2026-10-14 起，"不写死"原则）：
        //   佣金   = 用户档案（与券商的真实约定）> 旧策略 risk.commission_pct（兼容）> 万 2.5 兜底；
        //   印花税 = 官方生效日期表（risk.stamp_tax_pct 显式存在时尊重旧配置）；
        //   过户费 = 官方生效日期表（双边，此前缺失，现补上）。
        // 历史回放（同一结算日）自动落在当时的公告费率上。
        BigDecimal commission = commissionPctFor(strategy, risk);
        BigDecimal slippage = rate(field(risk, "slippage_pct"), DEFAULT_SLIPPAGE_PCT);
        BigDecimal stampTax = officialOrConfigured(risk, "stamp_tax_pct",
                MarketRulesService.RULE_STAMP_TAX_SELL, DEFAULT_STAMP_TAX_PCT, tradeDate);
        // 过户费在注册表引入（2026-10-14）之前不存在：不可用时按历史行为取 0，而不是编一个数
        BigDecimal transferFee = officialOrConfigured(risk, "transfer_fee_pct",
                MarketRulesService.RULE_TRANSFER_FEE, 0.0, tradeDate);
        BigDecimal lot = Money.price(numberOr(field(risk, "lot_size"),
                marketRulesService != null ? MarketRulesService.lotFor(strategy.getSymbol())
                        : defaultLotFor(strategy.getSymbol())));

        JsonNode positionConfig = config.get("position");
        String positionType = textOr(field(positionConfig, "type"), DEFAULT_POSITION_TYPE);
        BigDecimal sizePct = rate(field(positionConfig, "size_pct"), DEFAULT_SIZE_PCT);

        // agent 模式下"动多大"由模型给（size_fraction ∈ (0,1]），但**上限归调用方**——
        // trading_decision.py 的契约原话就是"上限由调用方（结算侧）裁决，不在这里放大"。
        // 所以这里把模型的比例当作 percent 语义的 size_pct 用，再被策略配置的上限夹一次：
        // 用户设的仓位约束永远压在模型之上，模型只能在它之内决定大小。
        //
        // 不认"非 agent 响应里的 size_fraction"：只有响应自己的指纹声明了 agent 才算数
        // （口径以 Python 为准，读不到才退回策略声明的模式），否则规则那条路的语义一点不变。
        BigDecimal agentSize = agentSizeFraction(strategy, result);
        if (agentSize != null && agentSize.signum() > 0) {
            BigDecimal cap = "percent".equals(positionType) ? sizePct : BigDecimal.ONE;  // full → 上限 100%
            if (agentSize.compareTo(cap) > 0) {
                // 夹住了就说话：否则"配置一直是绑定约束"这件事只会表现为
                // "模型的仓位判断好像从来不起作用"，而这正是最容易被误读成模型无能的情形。
                log.warn("Agent asked for size_fraction={} but strategy id={} caps it at {}; "
                        + "using the cap", agentSize, strategy.getId(), cap);
            }
            sizePct = agentSize.min(cap);
            positionType = "percent";
        }

        // 委员会的盘中授权上限（三层夹取的最后一层：策略 size_pct → agent size → 授权 max）。
        // 只会收紧不会放宽：授权是"边界"，不是"加成"。full 仓位在授权 <100% 时
        // 降级为 percent 语义，否则"上限"对 full 策略就成了空话。
        if (sizeCap != null && sizeCap.signum() >= 0) {
            boolean capTighter = "percent".equals(positionType)
                    ? sizePct.compareTo(sizeCap) > 0
                    : sizeCap.compareTo(BigDecimal.ONE) < 0;
            if (capTighter) {
                positionType = "percent";
                sizePct = sizeCap;
                log.info("Position capped by authorization for strategy id={}: {}",
                        strategy.getId(), sizeCap);
            }
        }

        String rawSignal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        String signal = rawSignal;
        // decision 是两侧共同的**最终裁决**字段（规则引擎的 _hold_facts 与委员会的 decide
        // 都会带）：decision=skip 表示"看过但不动"——委员会输出被截断只剩规范行、
        // size_fraction=0、HOLD、门控没开会、预算耗尽，全都落在这里。
        // 结算侧必须认它：只看 signal 会把"读不懂就不动"反转成意外成交
        // （signal=buy 但 size=0 的响应会被按配置仓位全额买入）。规则路径不受影响：
        // 规则引擎的 decision=skip 只在 signal=hold 时出现。痕迹仍记模型原话方向（rawSignal）。
        String decision = textOr(result.get("decision"), "").toLowerCase(Locale.ROOT);
        if (ExecutionContract.DECISION_SKIP.equals(decision)
                && (ExecutionContract.DECISION_BUY.equals(signal)
                        || ExecutionContract.DECISION_SELL.equals(signal))) {
            log.warn("Paper: decision=skip overrides signal={} for strategy id={} on {} "
                    + "(size=0/输出截断/HOLD)，本节不下单", signal, strategy.getId(), tradeDate);
            signal = "";
        }
        BigDecimal price = Money.price(priceOf(result));

        // 痕迹在**结算开始前**就建好（含账户的"结算前"状态与这根 bar 的证据）：
        // 这样每一条提前返回的分支都只是补上"结论 + 原因"，不会有哪条路悄悄漏掉痕迹。
        PaperTradeTrace trace = newTrace(strategy, account, settlementKind, trigger, tradeDate,
                barTime, result);
        trace.setSignal(rawSignal.isEmpty() ? null : rawSignal);
        trace.setMatchedConditions(joinMatchedConditions(result.get("matched_conditions")));

        // 没有可用价格就**不结算**（原样返回，什么都不改）。
        //
        // 这里以前会带着 price=0 继续走到底部的 updateEquityAndHighWatermark，
        // 于是"净值 = 现金 + 股数 × 0" = 现金 —— 一次瞬时取数失败就把持仓市值抹掉，
        // 而净值现在还会写进客观事实通道被长期记住。跳过才是正确行为：
        // 宁可不结算（并留下 skip 原因），也不要写一个错的净值。
        //
        // 原因优先采纳引擎/委员会给出的 skip_reason（如 agent_budget_exceeded ——
        // 预算分支的响应本来就没有 price，记成 invalid_price 会把"预算耗尽"永远藏起来），
        // 没有才按"取数失败 / 无效价格"兜底。
        if (price == null || price.signum() <= 0) {
            String reason = skipReasonOf(result,
                    result.has("error") ? ExecutionContract.SKIP_DATA_UNAVAILABLE
                            : ExecutionContract.SKIP_INVALID_PRICE);
            log.warn("Skipping paper settlement for strategy id={} on {}: no usable price "
                    + "(skip_reason={})", strategy.getId(), tradeDate, reason);
            return skip(account, trace, reason);
        }

        String reason = joinMatchedConditions(result.get("matched_conditions"));
        BigDecimal cash = orZero(account.getCash());
        // 卖前成本快照：批次账本的 legacy 近似要用（卖出后账户 avgCost 会被清零/摊薄）
        final BigDecimal avgCostBefore = orZero(account.getAvgCost());

        // 今天已经成交过：**只重估，不下单**，并且这一节照旧走完全程
        // （痕迹、账户、净值快照、客观事实、到期回填、当日报告都由调用方接着做完）。
        //
        // 为什么不能写成"规则没成立"：那天真正发生的事情是"已经动过了"。
        // 一个只有成交那天才有的原因被写成常态原因，审计与统计会同时失真 ——
        // 而这两个字段（decision / skip_reason）正是痕迹存在的理由。
        if (alreadyTradedToday) {
            trace.setDecision(ExecutionContract.DECISION_SKIP);
            trace.setSkipReason(ExecutionContract.SKIP_ALREADY_TRADED_TODAY);
            // 决策来源取**策略声明的模式**，而不是这份响应的指纹：这一节不是一次决策，
            // 而这份响应只是为了拿当日 bar（上面刻意走的规则端点）。
            // 按策略模式落库，才能让它和当天的其余记录落在同一段曲线上。
            trace.setDecisionMode(ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
            updateEquityAndHighWatermark(account, price);
            account.setLastPrice(price);
            // 刻意**不**覆盖 lastSignal：那应当是当天真正做出的那个信号（盘中成交时写的），
            // 而不是这一节用来估值的规则端点返回值。
            account.setLastEvalAt(LocalDateTime.now());
            fillAccountAfter(trace, account);
            paperTraceService.record(trace);
            account = paperAccountRepository.save(account);
            strategy.setLastPaperEvalAt(account.getLastEvalAt());
            strategyRepository.save(strategy);
            return account;
        }

        PaperTrade trade = null;
        if ("buy".equals(signal) && !isHolding(account)) {
            // 涨跌停挡单：参考价已到/越过当日涨停价时，真实市场排队买不进——模拟盘也不许成交。
            if (limitBlocked(strategy, "buy", price, tradeDate, result)) {
                log.info("Paper: buy blocked at limit-up for strategy id={} on {}",
                        strategy.getId(), tradeDate);
                return skip(account, trace, ExecutionContract.SKIP_LIMIT_BLOCKED);
            }
            // 成交价取整到 0.01 价位网格（买入向上取：真实挂单只能更贵，不能更便宜）。
            BigDecimal fill = tickRound(price.multiply(BigDecimal.ONE.add(slippage)), true);
            if (fill.signum() <= 0) {
                log.warn("Invalid fill price for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            }

            BigDecimal budget = "percent".equals(positionType)
                    ? Money.amount(cash.multiply(sizePct)) : cash;
            if (budget.signum() <= 0) {
                log.warn("No cash available for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }

            // 整手取整：按 (预算 / (成交价*(1+佣金+过户费)*手数)) **向下**取整手。
            // 用 divide(...,0,DOWN) 而不是 floor(double)：整手这件事的判定不能靠浮点。
            BigDecimal perLot = fill.multiply(
                    BigDecimal.ONE.add(commission).add(transferFee)).multiply(lot);
            BigDecimal shares = perLot.signum() <= 0 ? BigDecimal.ZERO
                    : budget.divide(perLot, 0, RoundingMode.DOWN).multiply(lot);
            while (shares.signum() > 0) {
                BigDecimal turnover = shares.multiply(fill);
                if (turnover.add(commissionFee(turnover, commission))
                        .add(Money.amount(turnover.multiply(transferFee))).compareTo(cash) <= 0) {
                    break;
                }
                shares = shares.subtract(lot);
            }
            if (shares.signum() <= 0) {
                // 这一条最容易"什么都没发生却没人知道"：涨得越高的票越容易撞上
                //（茅台一手 ~14 万 > 10 万本金），而且账户数字一动不动。
                log.warn("Not enough cash for even one lot on strategy id={} (fill={})",
                        strategy.getId(), fill);
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT);
            }
            // 成交量参与率上限：一根 bar 最多吃掉它成交量的 25%——真实市场里超过这个
            // 比例的单子会推着价格走（冲击成本），固定滑点模型盖不住。对大盘股满仓
            // 通常无感，小盘股/缩量 bar 会真实地限制单笔规模。
            shares = capByParticipation(shares, lot, result);
            if (shares.signum() <= 0) {
                log.info("Paper: buy volume-capped below one lot for strategy id={} on {}",
                        strategy.getId(), tradeDate);
                return skip(account, trace, ExecutionContract.SKIP_VOLUME_CAPPED);
            }
            BigDecimal turnover = shares.multiply(fill);
            BigDecimal fee = commissionFee(turnover, commission)
                    .add(Money.amount(turnover.multiply(transferFee)));   // 过户费双边
            BigDecimal cashAfter = Money.amount(cash.subtract(turnover).subtract(fee));
            if (cashAfter.signum() < 0) {
                log.warn("Buy would exceed cash on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }
            account.setShares(Money.price(shares));
            account.setAvgCost(fill);
            account.setCash(cashAfter);
            account.setHighWatermark(fill);
            // T+1 同交易日卖出守卫（realtime 按 bar 时间判断）。日线结算的 barTime 为 null，
            // 之前直接存 null 会让"当天日线买入、同日稍后盘中路径卖出"绕过 T+1 ——
            // 判定要求 lastBuyBar 非空且前 10 位（自然日）相同，所以这里落 tradeDate 本身。
            account.setLastBuyBar(barTime != null ? barTime : tradeDate.toString());
            trade = buildTrade(strategy, account, tradeDate, "BUY", fill, shares, reason);
        } else if ("sell".equals(signal) && isHolding(account)) {
            // 批次级 T+1（2026-10-15）：A 股的规则是"当日【买入的那一批】不可卖"，
            // 老批次当日可卖——不是"持有一天不能动"。有批次账本按账本算解锁份额；
            // 账本不可用（单测注入 null / 遗留账户）退回 lastBuyBar 的整仓判定。
            BigDecimal held = account.getShares();
            BigDecimal sellable = sellableShares(strategy, account, held, barTime, tradeDate);
            if (sellable.signum() <= 0) {
                log.debug("Paper: T+1 blocks sell — no unlocked lot (bar {}) for strategy id={}",
                        barTime, strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_T1_BLOCKED);
            }
            // 跌停挡单：跌停价上真实市场排队卖不出——模拟盘同样不成交。
            if (limitBlocked(strategy, "sell", price, tradeDate, result)) {
                log.info("Paper: sell blocked at limit-down for strategy id={} on {}",
                        strategy.getId(), tradeDate);
                return skip(account, trace, ExecutionContract.SKIP_LIMIT_BLOCKED);
            }
            // 成交价取整到 0.01 价位网格（卖出向下取：真实成交只能更便宜）。
            BigDecimal fill = tickRound(price.multiply(BigDecimal.ONE.subtract(slippage)), false);
            if (fill.signum() <= 0) {
                log.warn("Invalid fill price for sell on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            } else {
                BigDecimal shares = sellable;
                boolean partialSell = false;
                // agent 的减仓意图（size_fraction ∈ (0,1)）：按比例减、**向下取整到整手**。
                // 注意这里用的是原始 size_fraction，不被策略 size_pct/授权上限夹取 ——
                // 那两层是"开仓上限"，不该反过来限制卖出比例。取整后不足一手时按全量卖出
                //（上限是解锁份额：老批次全卖，当日批次留给明天）。
                BigDecimal sellFraction = agentSizeFraction(strategy, result);
                if (sellFraction != null && sellFraction.compareTo(BigDecimal.ONE) < 0) {
                    BigDecimal partial = held.multiply(sellFraction)
                            .divide(lot, 0, RoundingMode.DOWN).multiply(lot);
                    if (partial.signum() > 0 && partial.compareTo(shares) < 0) {
                        shares = partial;
                        partialSell = true;
                        log.info("Agent partial sell: fraction={} → {} of {} shares for strategy id={}",
                                sellFraction, shares.stripTrailingZeros().toPlainString(),
                                held.stripTrailingZeros().toPlainString(), strategy.getId());
                    }
                }
                // 成交量参与率上限（与买入对称）：缩量 bar 里的大卖单同样推价，按 25% 封顶。
                BigDecimal capped = capByParticipation(shares, lot, result);
                if (capped.signum() <= 0) {
                    log.info("Paper: sell volume-capped below one lot for strategy id={} on {}",
                            strategy.getId(), tradeDate);
                    return skip(account, trace, ExecutionContract.SKIP_VOLUME_CAPPED);
                }
                if (capped.compareTo(shares) < 0) {
                    shares = capped;
                    partialSell = true;   // 卖出侧的部分减仓：持仓保留，成本/水位口径同上
                }
                BigDecimal turnover = shares.multiply(fill);
                BigDecimal fee = commissionFee(turnover, commission)
                        .add(Money.amount(turnover.multiply(transferFee)));   // 过户费双边
                BigDecimal stampAmount = Money.amount(turnover.multiply(stampTax));
                BigDecimal cashAfter = Money.amount(cash.add(turnover).subtract(fee).subtract(stampAmount));
                account.setCash(cashAfter);
                if (partialSell) {
                    // 部分减仓：成本与最高水位口径不变（成本按买入时点摊定，剩余持仓照旧跟踪），
                    // lastBuyBar 保留 —— 剩余份额的 T+1 语义仍以最近一次买入日为准。
                    account.setShares(Money.price(held.subtract(shares)));
                } else {
                    account.setShares(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                    account.setAvgCost(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                    account.setHighWatermark(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                    account.setLastBuyBar(null);
                }
                trade = buildTrade(strategy, account, tradeDate, "SELL", fill, shares, reason);
            }
        } else if ("buy".equals(signal) || "sell".equals(signal)) {
            // 信号与账户状态对不上：已持仓时收到买入信号，或空仓时收到卖出信号。
            //
            // 结构上不该发生（引擎按 position 决定评估 entry 还是 exit），所以它一旦出现
            // 往往意味着并发或契约变更。**不能**把它写成"规则没成立"（那是假话），
            // 也不能继续往下走（下行分支按 signal 各自处理，会走到 else 把它标成 hold）。
            log.warn("Paper: signal={} contradicts account state (shares={}) for strategy id={}",
                    signal, account.getShares(), strategy.getId());
            return skip(account, trace, ExecutionContract.SKIP_STATE_MISMATCH);
        }

        updateEquityAndHighWatermark(account, price);
        account.setLastPrice(price);
        account.setLastSignal(signal);
        account.setLastEvalAt(LocalDateTime.now());

        // 成交落库（痕迹 → 成交 → 账户 → 策略）必须在**同一个短事务**里：
        // 之前三段各自独立提交，崩溃发生在"成交已落、账户未落"之间时，重跑同一根 bar
        // 会按旧账户状态（shares=0）再买一次 —— 成交表双行、账户单份持仓。
        // Python 调用此刻早已完成，这里没有网络等待，短事务不存在占连接等网络的问题
        // （与"结算整体不包长事务"的纪律不冲突：那是防 Python 的 60s 阻塞）。
        final PaperTrade tradeRef = trade;
        final PaperAccount accountRef = account;   // account 在事务结束后会被重新赋值，lambda 只认这份
        final BigDecimal txCommission = commission;   // 买建批要摊的费用，带进事务
        final BigDecimal txTransferFee = transferFee;
        final String txBarTime = barTime;
        final LocalDate txTradeDate = tradeDate;
        account = transactionTemplate.execute(status -> {
            if (tradeRef != null) {
                trace.setDecision(tradeRef.getSide().toLowerCase(Locale.ROOT));
                fillAccountAfter(trace, accountRef);
                PaperTradeTrace savedTrace = paperTraceService.record(trace);
                if (savedTrace != null) {
                    tradeRef.setTraceId(savedTrace.getId());
                }
                paperTradeRepository.save(tradeRef);
                // 批次账本与成交同事务（2026-10-15）：买入建批 / 卖出先进先出消耗。
                // 账本写失败不回滚成交（quietly 吞异常留日志）——账本是决策与计税层，
                // 账户与成交才是主账；下次卖出会走 legacy 近似并暴露在 realized_pnl 的口径上。
                if ("BUY".equalsIgnoreCase(tradeRef.getSide())) {
                    createLotForBuyQuietly(strategy, tradeRef, txCommission, txTransferFee,
                            txBarTime != null ? txBarTime : txTradeDate.toString());
                } else if ("SELL".equalsIgnoreCase(tradeRef.getSide())) {
                    consumeLotsFifoQuietly(strategy, tradeRef, avgCostBefore);
                }
            } else {
                // 信号是 hold：这一根 bar 的结论是"按策略不动"。
                // 原因由**引擎**给（它有指标值）：`rule_not_met` 与 `warmup` 是两件事，
                // 前者是"验证过、规则没动"，后者是"指标还没算出来、这段没有样本"。
                trace.setDecision(ExecutionContract.DECISION_SKIP);
                String resolvedReason = skipReasonOf(result, ExecutionContract.SKIP_RULE_NOT_MET);
                if (ExecutionContract.SKIP_AGENT_LLM_UNAVAILABLE.equals(resolvedReason)) {
                    alertLlmDegradedQuietly(strategy, null);   // 委员会侧的模型降级
                }
                trace.setSkipReason(resolvedReason);
                fillAccountAfter(trace, accountRef);
                paperTraceService.record(trace);
            }
            PaperAccount saved = paperAccountRepository.save(accountRef);
            strategy.setLastPaperEvalAt(saved.getLastEvalAt());
            strategyRepository.save(strategy);
            return saved;
        });
        if (tradeRef != null) {
            notifyFill(strategy, tradeRef);   // 推送旁路：事务外，失败只记日志
        }
        return account;
    }

    /**
     * LLM 持续降级的用户可见告警（2026-10-15）：模型不可用时模拟盘会安全地"持仓不动"，
     * 但用户不该要自己翻痕迹才发现"agent 今天全程没上班"。每小时最多一条
     * （幂等键带小时桶，saveReport 去重即节流），恢复后下一条自然不再发。
     */
    private void alertLlmDegradedQuietly(Strategy strategy, String detail) {
        if (messageService == null || strategy.getUser() == null) {
            return;
        }
        try {
            String hourBucket = LocalDateTime.now()
                    .format(java.time.format.DateTimeFormatter.ofPattern("yyyyMMddHH"));
            String content = String.format(
                    "【LLM 服务降级】%s · %s\n"
                            + "盯盘/委员会连续无法调用模型（%s），模拟盘已自动转为【持仓不动】：%n"
                            + "· 已有持仓：不买卖、只按行情重估，止损线照常生效（不依赖模型）%n"
                            + "· 新信号：留痕不执行，模型恢复后的下一根 K 线 / 15:30 结算自动复会%n"
                            + "· 每条「不动作」都有痕迹可查（原因：agent_llm_unavailable）%n"
                            + "排查：DeepSeek 状态页 / 服务端 LLM 重试与备用供应商日志。%n"
                            + "此告警每小时最多一条，恢复后不再发。",
                    strategy.getName(), strategy.getSymbol(),
                    detail == null ? "调用超时或服务过载" : detail);
            messageService.saveReport(strategy.getUser(),
                    StrategyHealthWatchService.TYPE_STRATEGY_SENTINEL,
                    "llm_degraded:" + strategy.getId() + ":" + hourBucket,
                    strategy.getSymbol(), content);
        } catch (Exception e) {
            log.debug("LLM degradation alert failed for strategy id={}: {}",
                    strategy.getId(), e.getMessage());
        }
    }

    /**
     * 成交回执推送（旁路）：失败只记日志。用户没配推送时是空操作。
     *
     * <p>2026-10-15 起不再写消息中心：中心口径已收窄为"预警 + 系统告警"
     * （CENTER_TYPES），PAPER_TRADE/PAPER_ALERT 类型落库后用户永远看不到，
     * 只是消息表里的死数据。成交的"页面里一定看得见的那一面"由成交列表
     * （/paper/trades）与痕迹页承担。
     */
    private void notifyFill(Strategy strategy, PaperTrade trade) {
        if (strategy.getUser() == null) {
            return;
        }
        try {
            String side = "BUY".equalsIgnoreCase(trade.getSide()) ? "买入" : "卖出";
            String title = String.format("模拟盘成交：%s %s", strategy.getName(), side);
            String body = String.format("%s %s %s 股 @ %s（%s 模式）",
                    trade.getTradeDate(), trade.getSymbol(),
                    trade.getShares() == null ? "-" : trade.getShares().stripTrailingZeros().toPlainString(),
                    trade.getPrice() == null ? "-" : trade.getPrice().toPlainString(),
                    ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
            if (pushService != null) {
                pushService.push(strategy.getUser().getUsername(), title, body);
            }
        } catch (Exception e) {
            log.debug("fill push failed for strategy {}: {}", strategy.getId(), e.getMessage());
        }
    }

    /**
     * 盘中语境（2026-10-14 复盘补）：当日已成交清单 + 上次全委会结论一句话。
     * 盯盘交易员动手前的"今天动过手吗、委员会最近怎么说"——缺了这两样，
     * 它会在不知道自己刚卖过的情况下把仓位买回来（当日实测踩过）。
     */
    private ObjectNode todayContextNode(Strategy strategy) {
        ObjectNode node = mapper.createObjectNode();
        try {
            var trades = paperTradeRepository
                    .findByStrategyIdAndTradeDateOrderByIdAsc(strategy.getId(), CnTime.today());
            if (!trades.isEmpty()) {
                ArrayNode arr = node.putArray("trades");
                for (PaperTrade t : trades) {
                    arr.addObject()
                            .put("side", t.getSide() == null ? "" : t.getSide().toLowerCase(Locale.ROOT))
                            .put("price", t.getPrice() == null ? null : t.getPrice().toPlainString())
                            .put("shares", t.getShares() == null ? null
                                    : t.getShares().stripTrailingZeros().toPlainString())
                            .put("time", t.getCreatedAt() == null ? "" : t.getCreatedAt().toString());
                }
            }
            for (PaperTradeTrace trace : paperTraceService.listRecent(strategy.getId(), 10)) {
                if (ExecutionContract.SETTLEMENT_DAILY.equals(trace.getSettlementKind())) {
                    var last = lastSettlementOf(trace);
                    if (last != null && last.sentence() != null && !last.sentence().isBlank()) {
                        node.put("last_committee", last.sentence());
                    }
                    break;
                }
            }
            // 开放批次：卖出动作要对着它卖（哪批、多老了、浮盈亏多少、今天锁没锁）
            var accountOpt = paperAccountRepository.findByStrategyId(strategy.getId());
            if (accountOpt.isPresent()) {
                ArrayNode lots = openLotsForContext(strategy.getId(),
                        orZero(accountOpt.get().getLastPrice()), CnTime.today());
                if (lots != null) {
                    node.set("open_lots", lots);
                }
            }
        } catch (Exception e) {
            log.debug("today context build failed for strategy {}: {}", strategy.getId(), e.getMessage());
        }
        return node;
    }

    /**
     * 信号触发了、但结算路径把它挡下了：把原因写进痕迹，账户一个字段都不改。
     *
     * <p>这是"7 条静默 return"的统一改写：以前它们对调用方完全不可见，
     * 现在每一条都会留下一行带原因的痕迹。
     */
    private PaperAccount skip(PaperAccount account, PaperTradeTrace trace, String skipReason) {        trace.setDecision(ExecutionContract.DECISION_SKIP);
        trace.setSkipReason(ExecutionContract.normalizeSkipReason(skipReason));
        fillAccountAfter(trace, account);
        paperTraceService.record(trace);
        return account;
    }

    /** 引擎回传的 `skip_reason`（它有指标值，比 Java 猜得准）；缺失或未知时用兜底值。 */
    private static String skipReasonOf(JsonNode result, String fallback) {
        String raw = result == null ? null : textOrStatic(result.get("skip_reason"), "");
        return ExecutionContract.normalizeSkipReason(raw == null || raw.isBlank() ? fallback : raw);
    }

    private static String textOrStatic(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    /**
     * 建一条痕迹：identity + 证据 + 账户的**结算前**状态。
     *
     * <p>证据全部取自 Python 回传的快照（`snapshot` / `fingerprint` / `fill_basis`）——
     * Java 不自己组装指标值，也不自己推口径：原则 4（单一真相源）。
     */
    private PaperTradeTrace newTrace(Strategy strategy, PaperAccount account, String settlementKind,
                                     String trigger, LocalDate tradeDate, String barTime,
                                     JsonNode result) {
        JsonNode snapshot = result == null ? null : result.get("snapshot");
        JsonNode fingerprint = result == null ? null : result.get("fingerprint");
        JsonNode bar = snapshot == null ? null : snapshot.get("bar");

        PaperTradeTrace trace = PaperTradeTrace.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .settlementKind(ExecutionContract.normalize(settlementKind,
                        ExecutionContract.SETTLEMENT_KINDS))
                .trigger(ExecutionContract.normalize(trigger, ExecutionContract.TRACE_TRIGGERS))
                .tradeDate(tradeDate)
                .barTime(barTime)
                .symbol(strategy.getSymbol())
                .repeatCount(1)
                .build();

        trace.setBarDate(textOrStatic(bar == null ? null : bar.get("date"), null));
        trace.setFillBasis(ExecutionContract.normalize(
                result == null ? null : textOrStatic(result.get("fill_basis"), null),
                ExecutionContract.FILL_BASES));
        // bar_close 与净值快照的 close_price 走同一个取价口径（priceOf：优先成交参考价、
        // 缺失回落 snapshot.bar.close）—— 之前这里只取 result.price，result.price 缺失的
        // 那天两张表的"close"一个 null 一个有值，对账时对不上。
        trace.setBarClose(Money.price(result == null ? null : priceOf(result)));
        trace.setEngineVersion(textOrStatic(fingerprint == null ? null : fingerprint.get("engine_version"), null));
        trace.setAdjustMode(ExecutionContract.normalize(
                textOrStatic(fingerprint == null ? null : fingerprint.get("adjust_mode"), null),
                ExecutionContract.ADJUST_MODES));
        trace.setMoneyPolicyVersion(fingerprint == null ? Money.POLICY_VERSION
                : intOr(fingerprint.get("money_policy_version"), Money.POLICY_VERSION));
        trace.setSnapshotJson(snapshot == null || snapshot.isNull() ? null : snapshot.toString());
        trace.setSnapshotSchemaVersion(snapshot == null || snapshot.isNull() ? null
                : intOr(snapshot.get("schema_version"), null));
        // 决策来源与 agent 成本：从**响应**里读（Python 是口径的唯一真相源），
        // 读不到才退回策略上声明的模式 —— 但绝不"默认成 rule"（见 normalizeDecisionMode）。
        trace.setDecisionMode(ExecutionContract.normalizeDecisionMode(
                textOrStatic(fingerprint == null ? null : fingerprint.get("decision_mode"),
                        strategy.getDecisionMode())));
        JsonNode committee = result == null ? null : result.get("committee");
        if (committee != null && committee.isObject()) {
            trace.setAgentLlmCalls(intOr(committee.get("llm_calls"), null));
            trace.setAgentTokens(intOr(committee.get("total_tokens"), null));
        }
        fillAccountBefore(trace, account);
        return trace;
    }

    /** 账户的**结算前**状态：痕迹要能回答"这一笔是从什么状态变成什么状态"。 */
    private void fillAccountBefore(PaperTradeTrace trace, PaperAccount account) {
        if (account == null) {
            return;
        }
        trace.setCashBefore(Money.amount(account.getCash()));
        trace.setSharesBefore(Money.price(account.getShares()));
        trace.setAvgCostBefore(Money.price(account.getAvgCost()));
        trace.setEquityBefore(Money.equity(account.getEquity()));
    }

    private void fillAccountAfter(PaperTradeTrace trace, PaperAccount account) {
        if (account == null) {
            return;
        }
        trace.setCashAfter(Money.amount(account.getCash()));
        trace.setSharesAfter(Money.price(account.getShares()));
        trace.setAvgCostAfter(Money.price(account.getAvgCost()));
        trace.setEquityAfter(Money.equity(account.getEquity()));
    }

    private static Double numberOrNull(JsonNode node) {
        return node == null || !node.isNumber() ? null : node.asDouble();
    }

    private static Integer intOr(JsonNode node, Integer fallback) {
        return node == null || !node.isNumber() ? fallback : node.asInt();
    }

    private void initializeAccount(PaperAccount account, Strategy strategy) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        BigDecimal initialCapital = Money.amount(numberOr(config.get("initial_capital"),
                DEFAULT_INITIAL_CAPITAL));

        if (account.getUser() == null) {
            account.setUser(strategy.getUser());
        }
        if (account.getStrategy() == null) {
            account.setStrategy(strategy);
        }
        if (account.getInitialCapital() == null) {
            account.setInitialCapital(initialCapital);
        }
        if (account.getCash() == null) {
            account.setCash(account.getInitialCapital());
        }
        if (account.getEquity() == null) {
            account.setEquity(account.getCash());
        }
        if (account.getShares() == null) {
            account.setShares(zeroPrice());
        }
        if (account.getAvgCost() == null) {
            account.setAvgCost(zeroPrice());
        }
        if (account.getHighWatermark() == null) {
            account.setHighWatermark(zeroPrice());
        }
    }

    private JsonNode buildPosition(PaperAccount account) {
        if (!isHolding(account)) {
            return null;
        }
        ObjectNode position = mapper.createObjectNode();
        position.put("entry_price", orZero(account.getAvgCost()));
        position.put("shares", orZero(account.getShares()));
        position.put("high_watermark", orZero(account.getHighWatermark()));
        // 持仓批次（2026-10-15）：agent 卖出决策第一次能对着具体批次说话——
        // "卖 10-14 那批成本 7.1271 的仓位（持有 1 天、浮亏 2.3%）"而不是"把持仓全抛了"。
        ArrayNode lots = openLotsForContext(account.getStrategy().getId(),
                orZero(account.getLastPrice()), CnTime.today());
        if (lots != null) {
            position.set("lots", lots);
        }
        return position;
    }

    /**
     * 净值与最高水位：**唯一**写这两个字段的地方。
     *
     * <p>恒等式 {@code equity = cash + shares × price} 在这里按 {@code Money} 的口径量化（2 位）——
     * 这是"逐笔对上账"的落点：不是"看起来差不多"，而是相等。
     */
    private void updateEquityAndHighWatermark(PaperAccount account, BigDecimal price) {
        BigDecimal shares = orZero(account.getShares());
        BigDecimal cash = orZero(account.getCash());
        account.setEquity(Money.equity(cash.add(shares.multiply(price))));

        if (shares.signum() > 0) {
            BigDecimal currentWatermark = account.getHighWatermark() == null
                    ? (account.getAvgCost() == null ? price : account.getAvgCost())
                    : account.getHighWatermark();
            if (price.compareTo(currentWatermark) > 0) {
                account.setHighWatermark(price);
            }
        }
    }

    private PaperTrade buildTrade(Strategy strategy, PaperAccount account, LocalDate tradeDate,
                                  String side, BigDecimal fill, BigDecimal shares, String reason) {
        return PaperTrade.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .tradeDate(tradeDate)
                .symbol(strategy.getSymbol())
                .side(side)
                .price(fill)
                .shares(Money.price(shares))
                .amount(Money.amount(shares.multiply(fill)))
                .reason(reason)
                .build();
    }

    private boolean isHolding(PaperAccount account) {
        return account.getShares() != null && account.getShares().signum() > 0;
    }

    /**
     * A 股佣金：费率>0 时按 {@code max(成交额 × 费率, 5 元)} 收取；费率=0（测试配置）不收。
     *
     * <p>5 元下限对**小额**成交是隐形门槛：一次只买 500 元时按 0.1% 本该收 0.5 元、实际收 5 元，
     * 单边成本就是 1%。这是真实券商的规则，所以照做（不"优化"掉），
     * 但它意味着小账户的每一笔都更贵 —— 这一点在报告与回测里都应当看得见。
     */
    private BigDecimal commissionFee(BigDecimal turnover, BigDecimal commission) {
        if (commission.signum() <= 0 || turnover.signum() <= 0) {
            return BigDecimal.ZERO.setScale(Money.AMOUNT_SCALE);
        }
        return Money.amount(turnover.multiply(commission)
                .max(BigDecimal.valueOf(DEFAULT_MIN_COMMISSION_YUAN)));
    }

    /**
     * 佣金的三级出处：用户档案（与券商的真实约定）> 旧策略 risk.commission_pct（兼容历史
     * 配置）> 万 2.5 兜底。档案里填了就以档案为准——"模拟即实盘"的费率应该跟着人走，
     * 不跟着某条策略当年写死的数字走。
     *
     * <p>用户按 ID 现查而不是 {@code strategy.getUser().getCommissionPct()}：
     * 结算不在事务里，关联的 User 多半是懒代理，直接取字段会抛
     * LazyInitializationException 把整笔结算炸掉；现查还顺带保证每次结算拿到
     * 用户最新改的费率。
     */
    private BigDecimal commissionPctFor(Strategy strategy, JsonNode risk) {
        try {
            if (strategy.getUser() != null) {
                Long userId = strategy.getUser().getId();   // 代理上可读，不触发初始化
                User user = userId == null ? null : userRepository.findById(userId).orElse(null);
                BigDecimal userPct = user == null ? null : user.getCommissionPct();
                if (userPct != null && userPct.signum() > 0) {
                    return Money.ratePct(userPct.doubleValue()).movePointLeft(2);
                }
            }
        } catch (Exception e) {
            log.debug("User commission lookup failed for strategy id={}: {} — falling back to config",
                    strategy.getId(), e.getMessage());
        }
        return rate(field(risk, "commission_pct"), DEFAULT_COMMISSION_PCT);
    }

    /**
     * 官方费率的查表优先：配置里**显式写了**该键（老配置兼容/测试置 0）就用配置值；
     * 没写就走 {@link MarketRulesService} 的生效日期表。历史回放时按结算日落在正确的
     * 公告区间上。注册表不可用（仅单测 null；生产构造注入恒存在）时退回
     * {@code unavailablePct}——它按"变更前的历史行为"取值（印花税 0.05 兜底、过户费 0），
     * 保证旧口径的单测语义不变。
     */
    private BigDecimal officialOrConfigured(JsonNode risk, String configKey, String ruleKey,
                                            double unavailablePct, LocalDate tradeDate) {
        JsonNode configured = field(risk, configKey);
        if (configured != null && configured.isNumber()) {
            return rate(configured, unavailablePct);
        }
        if (marketRulesService != null) {
            try {
                BigDecimal official = marketRulesService.ratePct(ruleKey, tradeDate);
                if (official != null && official.signum() >= 0) {
                    return Money.ratePct(official.doubleValue()).movePointLeft(2);
                }
            } catch (Exception e) {
                log.warn("Market fee lookup failed for {} ({}), using fallback {}%: {}",
                        ruleKey, tradeDate, unavailablePct, e.getMessage());
            }
        }
        return Money.ratePct(unavailablePct).movePointLeft(2);
    }

    /**
     * 成交价取整到 A 股 0.01 价位网格：买入向上（真实挂单只会更贵）、卖出向下（只会更便宜）。
     * 滑点乘出来的 4 位小数价格（如 6.7968）在真实市场不存在——这一步消掉它，
     * 且方向永远对模拟盘不利（保守侧），不会美化结果。
     */
    private static BigDecimal tickRound(BigDecimal rawFill, boolean roundUp) {
        BigDecimal units = rawFill.divide(MarketRulesService.TICK, 0,
                roundUp ? RoundingMode.CEILING : RoundingMode.FLOOR);
        return Money.price(units.multiply(MarketRulesService.TICK));
    }

    /** 单 bar 成交量参与率上限（25%）：模拟"大单推价"的冲击成本。 */
    private static final double VOLUME_PARTICIPATION_CAP = 0.25;

    /**
     * 按当根 bar 成交量的 25% 封顶成交股数（向下取整到整手）。
     * bar 无量（数据缺失）不设限：拿脏数据挡单比放行更糟。
     */
    private static BigDecimal capByParticipation(BigDecimal shares, BigDecimal lot, JsonNode result) {
        JsonNode bar = result == null || result.get("snapshot") == null
                ? null : result.get("snapshot").get("bar");
        JsonNode vol = bar == null ? null : bar.get("volume");
        if (vol == null || !vol.isNumber() || vol.asDouble() <= 0) {
            return shares;
        }
        BigDecimal cap = BigDecimal.valueOf(vol.asDouble() * VOLUME_PARTICIPATION_CAP)
                .setScale(0, RoundingMode.DOWN);
        if (shares.compareTo(cap) <= 0) {
            return shares;
        }
        return cap.divide(lot, 0, RoundingMode.DOWN).multiply(lot);
    }

    /**
     * 涨跌停挡单（**封死才挡**，2026-10-14 精细化）：只有当这根 bar 整根钉在停板价上
     * （high == low == 停板价，一字/封死未打开）才认定买不进/卖不出——真实市场里
     * 触板但盘中有打开的单子是可以成交的，之前"碰到停板价就挡"过于保守。
     * bar 高低点拿不到时退回旧口径（触板即挡）：信息不足时宁可保守。
     */
    private boolean limitBlocked(Strategy strategy, String side, BigDecimal price,
                                 LocalDate tradeDate, JsonNode result) {
        if (stockDataGateway == null || price == null || price.signum() <= 0) {
            return false;
        }
        try {
            BigDecimal prevClose = previousDailyClose(strategy.getSymbol(), tradeDate);
            if (prevClose == null || prevClose.signum() <= 0) {
                return false;
            }
            String name = null;
            try {
                name = stockDataGateway.getStockForSymbol(strategy.getSymbol()).name();
            } catch (Exception ignore) {
                // 名称拿不到只影响 ST 判定：按非 ST 的板块幅度算，宁可少挡不可乱挡
            }
            double limitPct = MarketRulesService.limitPctFor(strategy.getSymbol(), name);
            BigDecimal ratio = BigDecimal.valueOf(limitPct / 100.0);
            BigDecimal limitUp = Money.price(prevClose.multiply(BigDecimal.ONE.add(ratio))
                    .divide(MarketRulesService.TICK, 0, RoundingMode.HALF_UP)
                    .multiply(MarketRulesService.TICK));
            BigDecimal limitDown = Money.price(prevClose.multiply(BigDecimal.ONE.subtract(ratio))
                    .divide(MarketRulesService.TICK, 0, RoundingMode.HALF_UP)
                    .multiply(MarketRulesService.TICK));
            boolean atLimit = "buy".equals(side)
                    ? price.compareTo(limitUp) >= 0
                    : price.compareTo(limitDown) <= 0;
            if (!atLimit) {
                return false;
            }
            // 触板了：封死（bar 全程钉在停板价，high == low）才真挡；盘中打开过就放行
            JsonNode bar = result == null || result.get("snapshot") == null
                    ? null : result.get("snapshot").get("bar");
            BigDecimal high = barValue(bar, "high");
            BigDecimal low = barValue(bar, "low");
            if (high != null && low != null) {
                if (high.compareTo(low) != 0) {
                    return false;   // 盘中打开过 → 可成交
                }
                BigDecimal limitPrice = "buy".equals(side) ? limitUp : limitDown;
                return high.compareTo(limitPrice) == 0;   // 一字钉死在停板价 → 挡
            }
            return true;   // 拿不到高低点 → 退回保守口径（触板即挡）
        } catch (Exception e) {
            log.warn("Limit check failed for strategy id={} ({} {}): {} — fail-open",
                    strategy.getId(), side, tradeDate, e.getMessage());
            return false;
        }
    }

    private static BigDecimal barValue(JsonNode bar, String field) {
        if (bar == null) {
            return null;
        }
        JsonNode v = bar.get(field);
        return v != null && v.isNumber() ? Money.price(v.asDouble()) : null;
    }

    /** 结算日之前的最后一根日线收盘（涨跌停价的基准）。 */
    private BigDecimal previousDailyClose(String symbol, LocalDate tradeDate) {
        StockHistoryResponse history = stockDataGateway.getStockHistory(symbol, "day");
        if (history == null || history.timeSeries() == null) {
            return null;
        }
        BigDecimal best = null;
        LocalDate bestDate = null;
        for (Map.Entry<String, StockHistoryResponse.DailyPrice> e : history.timeSeries().entrySet()) {
            try {
                LocalDate d = LocalDate.parse(e.getKey().substring(0, 10));
                if (d.isBefore(tradeDate) && (bestDate == null || d.isAfter(bestDate))) {
                    BigDecimal close = new BigDecimal(e.getValue().close().trim());
                    if (close.signum() > 0) {
                        best = close;
                        bestDate = d;
                    }
                }
            } catch (Exception ignore) {
                // 脏行跳过
            }
        }
        return best;
    }

    /** A 股整手：科创板 688/689 起购 200 股/手，其余 100 股/手（可用 risk.lot_size 覆盖） */
    private double defaultLotFor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        return (s.startsWith("688") || s.startsWith("689")) ? 200.0 : 100.0;
    }

    // ==================== 持仓批次账本（2026-10-15） ====================

    /**
     * 当前可卖份额（批次级 T+1）：早于今天的未平批次之和；有批次但全被锁定 → 0（t1_blocked）。
     * **账本为空（没有任何开放批次）时退回 legacy**：lastBuyBar 同日 → 整仓锁定——
     * 覆盖批次功能上线前的遗留持仓，也避免"账本临时读挂"把合法卖出误锁。
     * 账户层持股数是上限（账本与账户不一致时以账户为准，宁可少卖不多卖）。
     */
    private BigDecimal sellableShares(Strategy strategy, PaperAccount account, BigDecimal held,
                                      String barTime, LocalDate tradeDate) {
        if (lotRepository != null) {
            try {
                List<PaperPositionLot> open = lotRepository
                        .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                                strategy.getId(), BigDecimal.ZERO);
                if (open != null && !open.isEmpty()) {
                    LocalDate day = barTime != null && barTime.length() >= 10
                            ? LocalDate.parse(barTime.substring(0, 10)) : tradeDate;
                    BigDecimal sum = BigDecimal.ZERO;
                    for (PaperPositionLot lot : open) {
                        if (lot.getOpenedDate().isBefore(day)) {
                            sum = sum.add(lot.getRemainingShares());
                        }
                    }
                    return sum.min(held);
                }
            } catch (Exception e) {
                log.warn("Lot-based T+1 check failed for strategy id={}: {} — falling back to legacy",
                        strategy.getId(), e.getMessage());
            }
        }
        boolean t1Blocked = barTime != null && account.getLastBuyBar() != null
                && sameTradingDay(barTime, account.getLastBuyBar());
        return t1Blocked ? BigDecimal.ZERO : held;
    }

    /** 买入建批（quietly：账本失败不影响成交主账）。 */
    private void createLotForBuyQuietly(Strategy strategy, PaperTrade buy,
                                         BigDecimal commission, BigDecimal transferFee, String openedBar) {
        if (lotRepository == null) {
            return;
        }
        try {
            BigDecimal turnover = buy.getAmount();
            BigDecimal fee = commissionFee(turnover, commission)
                    .add(Money.amount(turnover.multiply(transferFee)));
            LocalDateTime now = LocalDateTime.now();
            lotRepository.save(PaperPositionLot.builder()
                    .user(strategy.getUser())
                    .strategy(strategy)
                    .openedDate(buy.getTradeDate())
                    .openedBar(openedBar)
                    .tradeId(buy.getId())
                    .price(buy.getPrice())
                    .fee(fee)
                    .shares(buy.getShares())
                    .remainingShares(buy.getShares())
                    .createdAt(now)
                    .updatedAt(now)
                    .build());
            log.info("Lot opened for strategy id={}: {} shares @{} on {}",
                    strategy.getId(), buy.getShares().stripTrailingZeros().toPlainString(),
                    buy.getPrice().toPlainString(), buy.getTradeDate());
        } catch (Exception e) {
            log.warn("Lot creation failed for strategy id={} trade {}: {}",
                    strategy.getId(), buy.getId(), e.getMessage());
        }
    }

    /**
     * 卖出按【先进先出】消耗批次，把对冲明细（哪个批次多少股、批次成本）与本笔已实现
     * 盈亏写到成交行上。quietly：没有批次（遗留持仓）时按卖前移动加权成本近似，
     * realized_pnl 口径照样落——"这笔卖出落袋多少"永远有答案。
     *
     * <p>已实现盈亏按**毛利**（Σ(卖出价 − 批次成本) × 股数）落库：卖出费用已从现金扣除，
     * 再扣一次会造成口径歧义；净口径 = 毛利 − 卖出费用，由阅读端自行组合。
     */
    private void consumeLotsFifoQuietly(Strategy strategy, PaperTrade sell, BigDecimal avgCostBefore) {
        if (lotRepository != null) {
            try {
                List<PaperPositionLot> lots = lotRepository
                        .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                                strategy.getId(), BigDecimal.ZERO);
                BigDecimal need = sell.getShares();
                BigDecimal gross = BigDecimal.ZERO;
                ArrayNode matched = mapper.createArrayNode();
                for (PaperPositionLot lot : lots) {
                    if (need.signum() <= 0) {
                        break;
                    }
                    BigDecimal take = lot.getRemainingShares().min(need);
                    lot.setRemainingShares(Money.price(lot.getRemainingShares().subtract(take)));
                    lot.setUpdatedAt(LocalDateTime.now());
                    lotRepository.save(lot);
                    gross = gross.add(sell.getPrice().subtract(lot.getPrice()).multiply(take));
                    matched.addObject()
                            .put("lotId", lot.getId())
                            .put("openedDate", lot.getOpenedDate().toString())
                            .put("openedBar", lot.getOpenedBar() == null ? "" : lot.getOpenedBar())
                            .put("cost", lot.getPrice().toPlainString())
                            .put("shares", take.stripTrailingZeros().toPlainString());
                    need = need.subtract(take);
                }
                if (need.signum() > 0) {
                    // 账户与账本不一致的防御路径：没耗尽的份额按卖前均价补记，暴露在明细里
                    log.warn("Lots did not cover sell for strategy id={}: {} shares unmatched",
                            strategy.getId(), need.stripTrailingZeros().toPlainString());
                    gross = gross.add(sell.getPrice().subtract(avgCostBefore).multiply(need));
                }
                sell.setMatchedLots(matched.toString());
                sell.setRealizedPnl(Money.amount(gross));
                return;
            } catch (Exception e) {
                log.warn("FIFO consumption failed for strategy id={} sell {}: {} — approximating by avg cost",
                        strategy.getId(), sell.getId(), e.getMessage());
            }
        }
        sell.setRealizedPnl(Money.amount(
                sell.getPrice().subtract(avgCostBefore).multiply(sell.getShares())));
    }

    /** 开放批次摘要（给 agent 的上下文）：每批的年龄/成本/股数/浮盈亏/今日是否锁定。 */
    private ArrayNode openLotsForContext(Long strategyId, BigDecimal lastPrice, LocalDate today) {
        if (lotRepository == null) {
            return null;
        }
        try {
            List<PaperPositionLot> lots = lotRepository
                    .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                            strategyId, BigDecimal.ZERO);
            if (lots.isEmpty()) {
                return null;
            }
            ArrayNode arr = mapper.createArrayNode();
            for (PaperPositionLot lot : lots) {
                long age = today.toEpochDay() - lot.getOpenedDate().toEpochDay();
                ObjectNode o = arr.addObject();
                o.put("opened", lot.getOpenedDate().toString()
                        + (lot.getOpenedBar() == null ? "" : " " + lot.getOpenedBar()));
                o.put("age_days", age);
                o.put("cost", lot.getPrice().toPlainString());
                o.put("shares", lot.getRemainingShares().stripTrailingZeros().toPlainString());
                if (lastPrice != null && lastPrice.signum() > 0 && lot.getPrice().signum() > 0) {
                    o.put("pnl_pct", Money.amount(
                            lastPrice.divide(lot.getPrice(), 6, RoundingMode.HALF_UP)
                                    .subtract(BigDecimal.ONE).multiply(BigDecimal.valueOf(100))));
                }
                o.put("locked_today", lot.getOpenedDate().isEqual(today));
            }
            return arr;
        } catch (Exception e) {
            log.debug("open lots context failed for strategy {}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    // ==================== 分红除权（公司行动，2026-10-14） ====================
    /**
     * 分红除权调整：模拟盘"即实盘"的最后一块拼图。
     *
     * <h3>为什么必须有它</h3>日线历史是前复权、分钟成交是原始价：除权日不调整账户，
     * 现金红利凭空丢失，且除息缺口会让持仓市值"假跳水"（中行这种高分红股一次 ~2%），
     * trailing stop 还会被未调整的最高水位误触发——三项全是与实盘的硬偏差。
     *
     * <h3>调整口径</h3>
     * <ul>
     *   <li>现金红利：股数 × 每股派息 × (1 − 税率)。红利税差别化：持股 ≥1 年免税、
     *       1 个月–1 年 10%、&lt;1 个月 20%，按除权日已有持有期预扣（真实是卖出时
     *       按先进先出补扣——差异方向偏保守，量级 = 红利 × 税率差）；</li>
     *   <li>送股/转增：股数 × 送转比例，向下取整；不足一股的零头按除权参考价折现金
     *       （真实按除权日收盘价，此处用调整后成本近似）；</li>
     *   <li>成本：总成本不变 ÷ 新股数（只有送转摊薄，现金分红不动成本）；</li>
     *   <li>最高水位（移动止损基准）：(HWM − 每股派息) ÷ (1 + 送转比例)，地板 0——
     *       与交易所除权参考价同一公式，否则除息日会秒触发跟踪止损。</li>
     * </ul>
     *
     * <h3>幂等与适用范围</h3>痕迹键 {@code corporate_action:<strategy>:<exDate>}，
     * 已处理过的除权日永不重复入账；只调整【持仓起点（最近一次买入日）≤ 除权日 ≤ 今天】
     * 的行——买入之前发生的分红与本账户无关。账户调整与痕迹写入在同一短事务，
     * 半途失败整体回滚、下轮结算重试。
     */
    private PaperAccount applyCorporateActionsIfDue(Strategy strategy, PaperAccount account) {
        try {
            BigDecimal shares = orZero(account.getShares());
            if (shares.signum() <= 0) {
                return account;
            }
            String lastBuy = account.getLastBuyBar();
            if (lastBuy == null || lastBuy.length() < 10) {
                return account;   // 没有持仓起点，无从判断哪次除权"穿过"了这份持仓
            }
            LocalDate positionSince;
            try {
                positionSince = LocalDate.parse(lastBuy.substring(0, 10));
            } catch (Exception e) {
                return account;
            }
            LocalDate today = CnTime.today();
            List<DividendInfo> rows = loadDividends(strategy.getSymbol());
            for (DividendInfo div : rows) {
                if (div.exDate().isAfter(today) || div.exDate().isBefore(positionSince)) {
                    continue;
                }
                String dedupeKey = "corporate_action:" + strategy.getId() + ":" + div.exDate();
                account = applyOneCorporateAction(strategy, account, shares, div, dedupeKey,
                        positionSince);
                shares = orZero(account.getShares());   // 送转后股数变了，喂给下一行
            }
        } catch (Exception e) {
            log.warn("Corporate action check failed for strategy id={}: {} — fail-open（下轮重试）",
                    strategy.getId(), e.getMessage());
        }
        return account;
    }

    /** 单次除权的入账（痕迹 + 账户同一短事务；dedupe 命中直接返回，不动账）。 */
    private PaperAccount applyOneCorporateAction(Strategy strategy, PaperAccount account,
                                                 BigDecimal shares, DividendInfo div,
                                                 String dedupeKey, LocalDate positionSince) {
        BigDecimal cashBefore = orZero(account.getCash());
        BigDecimal avgBefore = orZero(account.getAvgCost());
        BigDecimal hwm = account.getHighWatermark();

        // 红利税差别化（2026-10-15 升级为批次加权）：持股 >1 年免税、1 个月–1 年 10%、
        // <1 个月 20%，按各批次在除权日的持有期加权——多批次建仓时不同批次税率不同，
        // 这正是"先进先出计股期"的精神（证监会深圳监管局投教案例）。真实规则是卖出时
        // 补扣，这里在除权日预扣：跨档差异对模拟盘偏保守（不会少扣）。
        double taxRate;
        long holdingDays;
        List<PaperPositionLot> openLots = null;
        if (lotRepository != null) {
            try {
                openLots = lotRepository
                        .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                                strategy.getId(), BigDecimal.ZERO);
            } catch (Exception e) {
                log.debug("open lots for corporate action failed: {}", e.getMessage());
            }
        }
        if (openLots != null && !openLots.isEmpty()) {
            BigDecimal taxedBase = BigDecimal.ZERO;   // Σ 股数 × 税率
            BigDecimal totalBase = BigDecimal.ZERO;
            for (PaperPositionLot lot : openLots) {
                long age = div.exDate().toEpochDay() - lot.getOpenedDate().toEpochDay();
                double r = age >= 365 ? 0.0 : (age >= 30 ? 0.10 : 0.20);
                taxedBase = taxedBase.add(lot.getRemainingShares()
                        .multiply(BigDecimal.valueOf(r)));
                totalBase = totalBase.add(lot.getRemainingShares());
            }
            taxRate = totalBase.signum() > 0
                    ? taxedBase.divide(totalBase, 4, RoundingMode.HALF_UP).doubleValue() : 0.0;
            holdingDays = div.exDate().toEpochDay()
                    - openLots.get(0).getOpenedDate().toEpochDay();   // 展示用：最早批次的持有天数
        } else {
            holdingDays = div.exDate().toEpochDay() - positionSince.toEpochDay();
            taxRate = holdingDays >= 365 ? 0.0 : (holdingDays >= 30 ? 0.10 : 0.20);
        }
        BigDecimal cashDividendNet = Money.amount(
                shares.multiply(div.cashPerShare())
                        .multiply(BigDecimal.ONE.subtract(BigDecimal.valueOf(taxRate))));

        BigDecimal bonusShares = BigDecimal.ZERO;
        BigDecimal oddLotCash = BigDecimal.ZERO;
        if (div.stockPerShare().signum() > 0) {
            BigDecimal rawBonus = shares.multiply(div.stockPerShare());
            bonusShares = rawBonus.setScale(0, RoundingMode.DOWN);
            BigDecimal oddLot = rawBonus.subtract(bonusShares);   // 不足一股的送转零头
            if (oddLot.signum() > 0) {
                // 零碎股折现金（真实按除权日收盘价；这里用除权参考价近似 = 调整后成本）
                BigDecimal refPrice = avgBefore.subtract(div.cashPerShare())
                        .divide(BigDecimal.ONE.add(div.stockPerShare()), 4, RoundingMode.HALF_UP);
                oddLotCash = refPrice.signum() > 0
                        ? Money.amount(oddLot.multiply(refPrice)) : BigDecimal.ZERO;
            }
        }
        BigDecimal sharesAfter = Money.price(shares.add(bonusShares));
        BigDecimal avgAfter = sharesAfter.signum() > 0
                ? Money.price(shares.multiply(avgBefore)
                        .divide(sharesAfter, 4, RoundingMode.HALF_UP))
                : avgBefore;
        BigDecimal hwmAdjusted = (hwm != null && hwm.signum() > 0)
                ? hwm.subtract(div.cashPerShare())
                        .divide(BigDecimal.ONE.add(div.stockPerShare()), 4, RoundingMode.HALF_UP)
                : null;
        final BigDecimal hwmAfter = hwmAdjusted != null && hwmAdjusted.signum() > 0
                ? Money.price(hwmAdjusted)
                : zeroPrice();
        final BigDecimal cashCredit = Money.amount(cashDividendNet.add(oddLotCash));

        PaperTradeTrace trace = newTrace(strategy, account, ExecutionContract.SETTLEMENT_DAILY,
                ExecutionContract.TRIGGER_CORPORATE_ACTION, div.exDate(),
                div.exDate().atStartOfDay().format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd")),
                null);
        trace.setDecision(ExecutionContract.DECISION_SKIP);
        trace.setSkipReason(ExecutionContract.SKIP_EX_DIVIDEND_DAY);
        trace.setCashBefore(cashBefore);
        trace.setSharesBefore(Money.price(shares));
        trace.setAvgCostBefore(avgBefore);
        trace.setEquityBefore(orZero(account.getEquity()));
        trace.setCashAfter(Money.amount(cashBefore.add(cashCredit)));
        trace.setSharesAfter(sharesAfter);
        trace.setAvgCostAfter(avgAfter);
        trace.setEquityAfter(null);   // 由调用方随本节结算重估，这里不猜
        try {
            ObjectNode snapshot = mapper.createObjectNode();
            snapshot.put("schema_version", 1);
            ObjectNode extra = snapshot.putObject("extra");
            extra.put("corporate_action", String.format(
                    "除权除息日 %s：每股派现 %s 元、每 10 股送转 %s 股（方案：%s）；"
                            + "红利税按持股期限预扣 %s%%（≥1年免税 / 1月–1年 10%% / <1月 20%%，"
                            + "持有 %d 天），送转零碎股按除权参考价折现",
                    div.exDate(), div.cashPerShare().toPlainString(),
                    div.stockPerShare().multiply(BigDecimal.TEN).stripTrailingZeros().toPlainString(),
                    div.plan, taxRate * 100, holdingDays));
            trace.setSnapshotJson(snapshot.toString());
            trace.setSnapshotSchemaVersion(1);
        } catch (Exception ignore) {
            // 快照是证据的注脚，写不进不拦入账
        }

        final PaperAccount acc = account;
        final List<PaperPositionLot> lotsToAdjust = openLots;
        return transactionTemplate.execute(status -> {
            if (!paperTraceService.recordOnceWithKey(trace, dedupeKey)) {
                return acc;   // 已处理过（或写失败）：不动账，幂等边界就在这条痕迹上
            }
            // 送转并入批次：股数 ×(1+ratio)、批次成本 ÷(1+ratio)——与除权参考价公式同源，
            // 批次的持有期继承原开仓日（真实规则：送转股计税基础继承原批次）。
            if (lotsToAdjust != null && div.stockPerShare().signum() > 0) {
                for (PaperPositionLot lot : lotsToAdjust) {
                    BigDecimal ratio = BigDecimal.ONE.add(div.stockPerShare());
                    lot.setRemainingShares(Money.price(lot.getRemainingShares().multiply(ratio)));
                    lot.setShares(Money.price(lot.getShares().multiply(ratio)));
                    BigDecimal adjPrice = lot.getPrice().subtract(div.cashPerShare())
                            .divide(ratio, 4, RoundingMode.HALF_UP);
                    lot.setPrice(adjPrice.signum() > 0 ? Money.price(adjPrice) : zeroPrice());
                    lot.setUpdatedAt(LocalDateTime.now());
                    lotRepository.save(lot);
                }
            }
            acc.setCash(Money.amount(orZero(acc.getCash()).add(cashCredit)));
            acc.setShares(sharesAfter);
            acc.setAvgCost(avgAfter);
            acc.setHighWatermark(hwmAfter);
            log.info("Corporate action applied for strategy id={} on {}: cash +{} (红利税 {}%), "
                            + "shares {} -> {}, avgCost {} -> {}, HWM {} -> {}",
                    strategy.getId(), div.exDate(), cashCredit, taxRate * 100,
                    shares.stripTrailingZeros().toPlainString(),
                    sharesAfter.stripTrailingZeros().toPlainString(),
                    avgBefore.toPlainString(), avgAfter.toPlainString(),
                    hwm == null ? "-" : hwm.toPlainString(), hwmAfter.toPlainString());
            return paperAccountRepository.save(acc);
        });
    }

    private List<DividendInfo> loadDividends(String symbol) {
        return dividendCache.get(symbol == null ? "" : symbol, s -> {
            try {
                JsonNode res = strategyClient.getDividends(s);
                JsonNode list = res == null ? null : res.get("dividends");
                if (list == null || !list.isArray()) {
                    return List.<DividendInfo>of();
                }
                List<DividendInfo> rows = new ArrayList<>();
                for (JsonNode row : list) {
                    try {
                        rows.add(new DividendInfo(
                                LocalDate.parse(textOr(row.get("ex_date"), "").substring(0, 10)),
                                decimalOrZero(row.get("cash_per_share")),
                                decimalOrZero(row.get("stock_per_share")),
                                textOr(row.get("plan"), "")));
                    } catch (Exception ignore) {
                        // 脏行跳过：一条解析失败不该让整只票失去除权保护
                    }
                }
                return rows;
            } catch (Exception e) {
                log.warn("Dividends load failed for {}: {}", s, e.getMessage());
                return List.<DividendInfo>of();
            }
        });
    }

    private static BigDecimal decimalOrZero(JsonNode node) {
        return node != null && node.isNumber() ? Money.price(node.asDouble())
                : BigDecimal.ZERO.setScale(Money.PRICE_SCALE);
    }

    private static BigDecimal zeroPrice() {
        return BigDecimal.ZERO.setScale(Money.PRICE_SCALE);
    }

    private static BigDecimal orZero(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    /** 配置里的费率/比例：百分数 → 小数（0.1 → 0.001）。缺省值走同一个入口，不两处各写一遍。 */
    private static BigDecimal rate(JsonNode node, double defaultPct) {
        return Money.ratePct(numberOr(node, defaultPct)).movePointLeft(2);
    }

    /**
     * agent 决策里的仓位比例（{@code size_fraction}，本来就是小数，**不再除以 100**）。
     *
     * <p>两条不认账的规矩，都是为了"宁可不动，也不要按错的仓位动"：
     * 只有响应自己的指纹声明 {@code decision_mode=agent} 才认（读不到指纹才退回策略声明的模式），
     * 且值必须是 (0,1] 的数字 —— 读不懂就返回 {@code null}，让配置的仓位口径原样生效。
     */
    private static BigDecimal agentSizeFraction(Strategy strategy, JsonNode result) {
        if (result == null || !result.isObject()) {
            return null;
        }
        String mode = ExecutionContract.normalizeDecisionMode(textOrStatic(
                result.path("fingerprint").path("decision_mode"),
                strategy == null ? null : strategy.getDecisionMode()));
        if (!ExecutionContract.DECISION_MODE_AGENT.equals(mode)) {
            return null;
        }
        BigDecimal size = Money.ratePct(numberOr(result.get("size_fraction"), null));
        return size != null && size.signum() > 0 && size.compareTo(BigDecimal.ONE) <= 0 ? size : null;
    }

    /**
     * 数字字段：不是数字就用缺省值（JSON 里可能是 null / "N/A" / 字符串）。
     *
     * <p>三元表达式里的 {@code fallback} **必须显式装箱**（写成 {@code Double.valueOf(...)}）：
     * 另一支是基本类型 {@code double}，二元数值提升会把 {@code Double} 拆箱，
     * 于是"缺省值传 null"的调用在**取值命中缺省那一支**时抛 NPE
     * （实测：agent 响应里没有 {@code size_fraction} 时结算整笔失败）。
     * 这种坑只在"确实走了缺省分支"的那天才炸，平时看不出任何异常。
     */
    private static Double numberOr(JsonNode node, Double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return Double.valueOf(node.asDouble());
    }

    /** bar 时间形如 "yyyy-MM-dd HH:mm:ss"（空格分隔，也容忍纯日期/缺秒）。解析失败返回 null。 */
    private static final java.time.format.DateTimeFormatter BAR_TIME_FORMAT =
            java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private static LocalDateTime parseBarTime(String barTime) {
        if (barTime == null || barTime.isBlank()) {
            return null;
        }
        // 兼容空格分隔（数据源实际格式）与 'T' 分隔（ISO）；缺秒补秒
        String t = barTime.trim().replace('T', ' ');
        try {
            if (t.length() == 10) {
                return LocalDate.parse(t).atStartOfDay();
            }
            return LocalDateTime.parse(t.length() == 16 ? t + ":00" : t, BAR_TIME_FORMAT);
        } catch (Exception e) {
            return null;
        }
    }

    /** bar 时间形如 "yyyy-MM-dd HH:mm:ss"，按自然日前 10 位判断是否同一交易日 */
    private static boolean sameTradingDay(String barA, String barB) {
        if (barA == null || barB == null || barA.length() < 10 || barB.length() < 10) {
            return false;
        }
        return barA.substring(0, 10).equals(barB.substring(0, 10));
    }

    private JsonNode parseConfig(String configJson) {
        try {
            if (configJson == null || configJson.isBlank()) {
                return mapper.createObjectNode();
            }
            return mapper.readTree(configJson);
        } catch (Exception e) {
            log.warn("Failed to parse strategy config JSON: {}", e.getMessage());
            return mapper.createObjectNode();
        }
    }

    private String textOr(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    private JsonNode field(JsonNode node, String name) {
        return node == null ? null : node.get(name);
    }

    private String joinMatchedConditions(JsonNode node) {
        if (node == null || !node.isArray()) {
            return "";
        }
        List<String> conditions = new ArrayList<>();
        for (JsonNode condition : node) {
            if (!condition.isNull()) {
                conditions.add(condition.asText());
            }
        }
        return String.join(",", conditions);
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }

    private Strategy loadStrategy(User user, Long strategyId) {
        return strategyRepository.findByIdAndUserId(strategyId, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
    }
}
