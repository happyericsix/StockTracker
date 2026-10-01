# 股票资讯领域模型微调管线

> 目标（用户原话）：*"python 侧最好是可以去寻找开源大模型来训练模型，
> 以达到最适配股票行业的模式——一个专门为股票信息分析而训练的 agent 或 LLM。"*

这份文档回答三件事：**为什么要微调、怎么微调、什么时候允许上线**。
架构分工不变：Python 处理数据与模型，Java 管业务与数据出口。

---

## 0. 三阶段演进（先想清楚再动手）

| 阶段 | 形态 | 成本 | 什么时候值得 |
|---|---|---|---|
| **S0（现状）** | DeepSeek 通用模型 + 领域 prompt + 规则引擎（news_credibility） | ¥ API 费 | **现在就在跑**，且效果已可用 |
| **S1** | 同上 + RAG（把本项目已解读的 800+ 条资讯作为检索语料注入上下文） | 低 | 数据 < 3k 条时的性价比之王 |
| **S2** | LoRA 微调开源基座（本文档主体），vLLM 部署，OpenAI 兼容端点换入 | 需 GPU（见 §3） | 已解读数据 ≥ 3k 条，或想摆脱 API 依赖/降本 |

**顺序不可跳**：S2 的训练数据（已规整的分析结论）正是 S0/S1 运行时攒下来的。
先微调再上线 = 没有监督信号的蒸馏，只能得到一个更小的通用模型。

## 1. 为什么选 LoRA 微调而不是从零训练 / 继续预训练

- **任务不是"学会中文金融语言"，是"学会本项目的输出契约"**：
  spec §6 的枚举字段、`direction=null ≠ 中性` 的语义、"该保守时保守"的
  置信度纪律。这是 7B 级模型的 LoRA 可 teaching 的，不需要重训世界知识。
- LoRA 参数量 ~0.5%，单卡 24G（RTX 4090 / A10）可训 7B；消费级
  RTX 3090/4090 用 QLoRA 4bit 亦可（见 train_lora.py 的注释）。
- 从零/继续预训练需要百万级金融语料 + 多卡天级预算，个人项目不成立。

### 基座选型（2026-10 口径，选型时请复核）

| 模型 | 理由 | 协议 |
|---|---|---|
| **Qwen3-8B-Instruct**（首选） | 中文金融语料覆盖最好的开源档位；Apache-2.0 可商用；ChatML 模板与 LLaMA-Factory 无缝 | Apache-2.0 |
| Qwen3-4B-Instruct | 显存不足时的退档，中文质量同族 | Apache-2.0 |
| GLM-4-9B-Chat | 备选，中文强；注意工具调用模板差异 | Apache-2.0（附条件，部署前核对当时的 LICENSE） |

**协议必须逐版核对**——开源模型换版改协议是常态（参考调研里
TradingAgents-CN 的 `app/` 是专有协议的教训）。

## 2. 数据策略：三个任务，一份导出

数据源 = Java 内部导出接口 `GET /api/v1/internal/news/training-export`
（InternalNewsController，内部令牌保护，只出**已解读**条目 + 可信度标注）。
**不在 Python 侧直连 MySQL**——与记忆系统同一条"agent 不碰数据层"纪律。

`build_dataset.py` 把每条导出样本构造成 **ShareGPT 格式**（LLaMA-Factory 通用），
一个样本三个任务视角（防止模型把"判方向"学成唯一技能）：

| 任务 | instruction（用户侧） | output（监督信号） | 信号来源 |
|---|---|---|---|
| A 结构化解读 | 股票资讯 → 给 spec §6 JSON | `analysis` 字段 | DeepSeek 已规整结论（蒸馏） |
| B 传闻识别 | 同一条 → `{"rumor": bool, "reason": …}` | `credibility_grade` + rumor 语义 | **规则引擎当老师**（弱监督，可复现） |
| C 拒答纪律 | 信息不足的条目 → "不判断方向" | 降级样本（单独构造） | normalize_analysis 的硬约束 |

> **诚实边界**：A 任务的标签是 DeepSeek 的输出，微调学的是
> "本项目当前的解读风格与纪律"，不是"客观正确的方向判断"。
> 若 DeepSeek 系统性判错，微调会放大同样的错——所以 §5 的评估
> 里方向准确率必须对着**人审过的金标集**，不是对着 DeepSeek 自身。

### 数据量预期（决定你现在该在哪一阶段）

- < 500 条已解读：别微调。S1 的 RAG 直接用这批数据更好。
- 500–3k 条：可训，但只训任务 B/C（规则引擎标签无限可再生，最稳），
  任务 A 用低学习率防过拟合。
- \> 3k 条：三任务全开，8B QLoRA 3 epoch，单卡几小时。

## 3. 训练与部署链路

```
Java /internal/news/training-export          （只读，已解读 + 可信度）
        │  X-Internal-Token
        ▼
finetune/build_dataset.py ──► dataset/train.jsonl + val.jsonl（ShareGPT）
        │
        ▼
finetune/train_lora.py ──► adapters/stock-analyst-lora-rN   （需要 GPU）
        │                     ↑ LLaMA-Factory（llamafactory-cli train）
        ▼
vLLM serve Qwen/Qwen3-8B-Instruct --enable-lora --lora-modules stock-analyst=…
        │  OpenAI 兼容：http://localhost:8001/v1
        ▼
finetune/eval_domain.py ──► 必须过 §5 的门槛，否则不许换入
        │
        ▼（过了才换）
python .env: DEEPSEEK_BASE_URL=http://localhost:8001  DEEPSEEK_MODEL=stock-analyst
        （llm_service 已是 OpenAI 兼容客户端，零代码改动；先灰度见 §6）
```

## 4. 脚本清单

| 脚本 | 干什么 | 依赖 |
|---|---|---|
| `build_dataset.py` | 拉导出接口 → 三任务样本 → train/val 切分（按**时间**切，防泄漏） | 仅 stdlib + urllib |
| `train_lora.py` | 调 LLaMA-Factory 跑 QLoRA；**无 GPU/未安装时明确拒绝运行**而不是装样子 | GPU + LLaMA-Factory |
| `eval_domain.py` | 金标集评估任意 OpenAI 兼容端点（基座/微调/DeepSeek 都能评） | 仅 stdlib |
| `cases.yaml` | 人审金标：方向 + 传闻 + 拒答三类用例（只放**人工确认过**的） | — |

## 5. 上线门槛（不过不换）

对 `cases.yaml` 的金标集，候选模型必须同时满足：

1. **schema 合法率 ≥ 95%**：输出能被 normalize_analysis 规整为合法枚举
   （微调最容易学会的就是格式，这条不过说明训练本身失败）；
2. **方向准确率 ≥ DeepSeek 基线 - 3pt**：微调的意义在成本/延迟/隐私，
   **不在准确率**——如果方向还变差了，微调就没有存在理由；
3. **校准不劣化**：把金标按模型 confidence 分桶，高置信桶准确率 ≥ 低置信桶
   （置信度必须携带信息，否则 MIN_CONFIDENCE=0.4 的下游逻辑全废）;
4. **拒答纪律**：构造的信息不足样本，"不判断方向"率 ≥ 90%
   （宁可少判，不可瞎判——这是 spec §6 的硬约束，也是产品信誉）。

## 6. 灰度与回滚

- `llm_service` 的 BASE_URL/MODEL 已环境变量化：换模型 = 改 `.env` 重启，
  **回滚 = 改回来**，不碰代码。
- 建议灰度顺序：先只切 `news_analyst` 批量分类任务（失败有降级链兜底），
  跑一周对比 `analyzed` 成功率与人工抽检，再考虑 deep_dive / 综合解读。
- vLLM 起在本机 8001，**不要**暴露到公网；它与 FastAPI(8000) 无鉴权冲突
  （前者只由 llm_service 服务端调用）。

## 7. 已知放弃的东西

- 微调后模型**不再自动跟进** DeepSeek 的新能力（改版、新风格）——
  这是换自托管模型的固有代价，评估脚本要定期重跑。
- 多用户并发下 vLLM 吞吐 < 商用 API；本项目单用户场景无碍，
  将来多租户时重新评估。
- 训练数据含全部历史资讯（含其它标的），**不含用户身份数据**
  （导出接口没有 userId 维度，这是刻意的）。
