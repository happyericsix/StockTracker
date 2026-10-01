"""build_dataset.py —— 从 Java 内部导出接口构建微调数据集。

用法（在 python-data-service/ 目录）：
    # .env 里已有 INTERNAL_API_TOKEN 与 SPRING_BASE_URL 时：
    python finetune/build_dataset.py --out finetune/dataset

    # 或显式指定：
    python finetune/build_dataset.py --base http://localhost:8080 --days 180

产出：train.jsonl / val.jsonl（ShareGPT 格式，LLaMA-Factory 直接可用）。

<h3>切分纪律：按时间切，不随机切</h3>
资讯有强时效性，随机切分会让"同一事件的后续报道"同时出现在训练/验证集，
验证分数虚高（模型背过这件事，不是会判这件事）。这里取最近 10% 做验证，
其余做训练——验证集永远比训练集"新"，量出来的才是泛化。

<h3>为什么用 stdlib 而不是 requests</h3>
这个脚本跑在训练机上而不是服务里；少一个依赖，"clone 下来就能跑"的
摩擦小一分。规则与 scripts/ 下的手工脚本一致。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.request

DEFAULT_BASE = os.getenv("SPRING_BASE_URL", "http://localhost:8080").rstrip("/")
DEFAULT_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

# ShareGPT 系统提示：与 prompts/news_analyst.md 的任务定义同源。
# 刻意不复用整篇 prompt（那是给 API 模型的完整版）；微调要教的是
# "短指令 → 稳定输出"，指令越长，模型学到的越是"读长文"而不是"判资讯"。
SYSTEM_ANALYZE = (
    "你是A股资讯分析师。读给定的股票资讯，只输出JSON："
    '{"event_type":…, "direction":…, "confidence":…, "impact_level":…, '
    '"plain_summary":…}。direction只能是利好/利空/中性之一；'
    "信息不足时direction为null且confidence低于0.4，不要编造方向。"
)
SYSTEM_RUMOR = (
    "你是资讯核查助手。判断给定资讯是否带传闻特征（据传/或将/知情人士等措辞，"
    '且非官方公告渠道）。只输出JSON：{"rumor": true/false, "reason": "一句话依据"}。'
)

VALID_EVENT_TYPES = {"业绩预告", "重大合同", "回购", "增减持", "监管处罚", "重组",
                     "宏观政策", "行业动态", "其他"}
VALID_DIRECTIONS = {"利好", "利空", "中性", ""}  # "" = direction 为 null 的样本


def load_env_if_present() -> None:
    """训练机上通常没有服务进程的 .env 上下文，尝试从上级目录读一份。"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    for line in io.open(env_path, encoding="utf-8-sig", errors="ignore"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def fetch_export(base: str, token: str, days: int, limit: int) -> list[dict]:
    url = f"{base}/api/v1/internal/news/training-export?days={days}&limit={limit}"
    request = urllib.request.Request(url, headers={"X-Internal-Token": token})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("items", [])


def sample_task_analyze(row: dict) -> dict | None:
    """任务 A：结构化解读（蒸馏已规整的 DeepSeek 结论）。"""
    analysis = row.get("analysis") or {}
    direction = str(analysis.get("direction") or "")
    event_type = str(analysis.get("event_type") or "")
    summary = str(analysis.get("plain_summary") or "").strip()
    if event_type not in VALID_EVENT_TYPES or direction not in VALID_DIRECTIONS or not summary:
        return None
    target = {
        "event_type": event_type,
        "direction": direction or None,
        "confidence": round(float(analysis.get("confidence") or 0.0), 2),
        "impact_level": str(analysis.get("impact_level") or "low"),
        "plain_summary": summary,
    }
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_ANALYZE},
            {"from": "human", "value": user_prompt(row)},
            {"from": "gpt", "value": json.dumps(target, ensure_ascii=False)},
        ],
        "task": "analyze",
    }


def sample_task_rumor(row: dict) -> dict:
    """任务 B：传闻识别（规则引擎当老师——标签可复现、可解释、可再生成）。"""
    grade = str(row.get("credibility_grade") or "")
    rumor = grade in ("低", "较低") and int(row.get("source_level") or 0) != 1
    reason = ("信源与措辞信号显示可信度较低，含传闻特征" if rumor
              else "未见明显传闻特征")
    target = {"rumor": rumor, "reason": reason}
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_RUMOR},
            {"from": "human", "value": user_prompt(row)},
            {"from": "gpt", "value": json.dumps(target, ensure_ascii=False)},
        ],
        "task": "rumor",
    }


def sample_task_refusal(row: dict) -> dict | None:
    """任务 C：拒答纪律——只从真实降级样本出，不人工编造。"""
    analysis = row.get("analysis") or {}
    summary = str(analysis.get("plain_summary") or "")
    if "不判断方向" not in summary:
        return None
    target = {
        "event_type": "其他",
        "direction": None,
        "confidence": 0.3,
        "impact_level": "low",
        "plain_summary": "信息不足，不判断方向",
    }
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_ANALYZE},
            {"from": "human", "value": user_prompt(row)},
            {"from": "gpt", "value": json.dumps(target, ensure_ascii=False)},
        ],
        "task": "refusal",
    }


def user_prompt(row: dict) -> str:
    source = {1: "官方公告", 2: "媒体", 3: "研报", 4: "舆情"}.get(row.get("source_level"), "资讯")
    symbol = str(row.get("symbol") or "市场")
    title = str(row.get("title") or "").strip()
    content = str(row.get("content") or "").strip()
    return f"【信源】{source}\n【标的】{symbol}\n【标题】{title}\n【正文】{content}"


def build(rows: list[dict]) -> tuple[list[dict], dict]:
    samples: list[dict] = []
    stats = {"analyze": 0, "rumor": 0, "refusal": 0, "skipped": 0}
    for row in rows:
        made = False
        for builder in (sample_task_analyze, sample_task_refusal):
            sample = builder(row)
            if sample is not None:
                samples.append(sample)
                stats[sample["task"]] += 1
                made = True
                break  # analyze 与 refusal 互斥（一条资讯只有一个解读目标）
        rumor_sample = sample_task_rumor(row)
        samples.append(rumor_sample)
        stats["rumor"] += 1
        if not made and rumor_sample is None:
            stats["skipped"] += 1
    return samples, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="构建股票资讯微调数据集")
    parser.add_argument("--base", default=DEFAULT_BASE, help="Java 后端地址")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="内部令牌（默认读环境变量）")
    parser.add_argument("--days", type=int, default=180, help="取最近 N 天")
    parser.add_argument("--limit", type=int, default=2000, help="最多取多少条")
    parser.add_argument("--val-ratio", type=float, default=0.1, help="验证集占比（按时间取最新的部分）")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "dataset"),
                        help="输出目录")
    args = parser.parse_args()

    load_env_if_present()
    token = args.token or os.getenv("INTERNAL_API_TOKEN", "")
    if not token:
        print("✗ INTERNAL_API_TOKEN 未配置（.env 或 --token）", file=sys.stderr)
        return 2

    print(f"[*] 拉取导出：{args.base} days={args.days} limit={args.limit}")
    rows = fetch_export(args.base, token, args.days, args.limit)
    print(f"[*] 取回 {len(rows)} 条已解读事件")

    samples, stats = build(rows)
    print(f"[*] 样本统计：{stats}")

    # 按时间切：导出本身按 published_at 倒序，前 val_ratio 即"最新的一部分"
    val_count = max(1, int(len(samples) * args.val_ratio)) if samples else 0
    val, train = samples[:val_count], samples[val_count:]

    os.makedirs(args.out, exist_ok=True)
    for name, subset in (("train.jsonl", train), ("val.jsonl", val)):
        path = os.path.join(args.out, name)
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            for sample in subset:
                # task 字段是我们自己的记账列，LLaMA-Factory 不认，剔除后再落盘
                clean = {"conversations": sample["conversations"]}
                handle.write(json.dumps(clean, ensure_ascii=False) + "\n")
        print(f"[*] 写出 {path}（{len(subset)} 条）")

    print("✓ 数据集就绪。下一步：README §3 的 train_lora.py（需要 GPU + LLaMA-Factory）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
