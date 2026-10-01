"""eval_domain.py —— 领域金标集评估：任意 OpenAI 兼容端点都能评。

用法（评谁就把 BASE/MODEL 指向谁）：
    # 评当前线上 DeepSeek（基线）
    python finetune/eval_domain.py --base https://api.deepseek.com --model deepseek-chat

    # 评微调后的 vLLM
    python finetune/eval_domain.py --base http://localhost:8001 --model stock-analyst

读 `cases.yaml`（人审金标），输出 README §5 的四道门槛：
schema 合法率 / 方向准确率 / 校准单调性 / 拒答率。

与 tests/memory_eval、tests/tool_eval 同一门第：**评估要可复现、
结论要能被质疑**——每次运行输出逐 case 的判定，不只是总分，
否则"85 分"和"运气好"无法区分。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# 与 build_dataset.py 同源的短指令（评估必须用与训练一致的提示分布，
# 否则量的是"提示词鲁棒性"而不是"模型能力"）
SYSTEM_ANALYZE = (
    "你是A股资讯分析师。读给定的股票资讯，只输出JSON："
    '{"event_type":…, "direction":…, "confidence":…, "impact_level":…, '
    '"plain_summary":…}。direction只能是利好/利空/中性之一；'
    "信息不足时direction为null且confidence低于0.4，不要编造方向。"
)
VALID_DIRECTIONS = {"利好", "利空", "中性"}
VALID_EVENT_TYPES = {"业绩预告", "重大合同", "回购", "增减持", "监管处罚", "重组",
                     "宏观政策", "行业动态", "其他"}
JSON_BLOCK = re.compile(r"\{.*\}", re.S)

# README §5 的四道门槛（换入生产前必须全过）
GATES = {
    "schema_valid_rate": 0.95,
    "direction_accuracy_floor_delta": -0.03,   # 相对基线最多允许掉 3pt
    "calibration_monotonic": True,             # 高置信桶准确率 ≥ 低置信桶
    "refusal_rate": 0.90,
}


def load_cases() -> list[dict]:
    """读 cases.yaml。用 stdlib 的极简 YAML 子集解析（列表 of dict、标量），
    避免为评估脚本引入 pyyaml 依赖。"""
    path = os.path.join(HERE, "cases.yaml")
    cases: list[dict] = []
    current: dict | None = None
    for raw in io.open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.startswith("- id:") or (line.startswith("- ") and current is None and "id:" in line):
            if current:
                cases.append(current)
            current = {}
            line = line[2:]
        if current is None or not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and ":" in line and not line.strip().startswith("- "):
            key, _, value = line.strip().partition(":")
            value = value.strip().strip('"').strip("'")
            current[key.strip()] = value
    if current:
        cases.append(current)
    return cases


def call_endpoint(base: str, model: str, token: str, user_prompt: str,
                  max_retries: int = 2) -> str | None:
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_ANALYZE},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,   # 分类任务：低温才可复现
        "max_tokens": 400,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base.rstrip('/')}/v1/chat/completions"

    for attempt in range(max_retries + 1):
        try:
            request = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
            if attempt == max_retries:
                print(f"  ✗ 调用失败: {exc}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def parse_output(text: str | None) -> dict | None:
    if not text:
        return None
    match = JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def is_schema_valid(output: dict) -> bool:
    direction = output.get("direction")
    direction_ok = direction is None or str(direction) in VALID_DIRECTIONS
    event_ok = str(output.get("event_type")) in VALID_EVENT_TYPES
    try:
        confidence = float(output.get("confidence"))
        conf_ok = 0.0 <= confidence <= 1.0
    except (TypeError, ValueError):
        conf_ok = False
    summary_ok = bool(str(output.get("plain_summary") or "").strip())
    return direction_ok and event_ok and conf_ok and summary_ok


def user_prompt_for(case: dict) -> str:
    return (f"【信源】{case.get('source', '资讯')}\n【标的】{case.get('symbol', '市场')}\n"
            f"【标题】{case['title']}\n【正文】{case.get('content', '')}")


def evaluate(base: str, model: str, token: str, cases: list[dict]) -> dict:
    results = {"schema_valid": 0, "direction_total": 0, "direction_correct": 0,
               "refusal_total": 0, "refusal_correct": 0,
               "buckets": {"low": [0, 0], "high": [0, 0]},  # [correct, total]
               "details": []}
    for case in cases:
        text = call_endpoint(base, model, token, user_prompt_for(case))
        output = parse_output(text)
        kind = case.get("kind", "direction")
        detail = {"id": case.get("id"), "kind": kind, "raw_ok": output is not None}

        if output is not None and is_schema_valid(output):
            results["schema_valid"] += 1
            detail["schema_valid"] = True
        else:
            detail["schema_valid"] = False

        if kind == "refusal":
            results["refusal_total"] += 1
            refused = output is None or output.get("direction") in (None, "", "null")
            if refused:
                results["refusal_correct"] += 1
            detail["refused"] = refused
        elif output is not None and output.get("direction") in VALID_DIRECTIONS:
            results["direction_total"] += 1
            correct = str(output["direction"]) == case.get("expect_direction")
            if correct:
                results["direction_correct"] += 1
            detail["predicted"] = output.get("direction")
            detail["expected"] = case.get("expect_direction")
            # 校准桶：confidence ≥ 0.7 为高桶
            try:
                bucket = "high" if float(output.get("confidence") or 0) >= 0.7 else "low"
            except (TypeError, ValueError):
                bucket = "low"
            results["buckets"][bucket][1] += 1
            if correct:
                results["buckets"][bucket][0] += 1
        results["details"].append(detail)
    return results


def report(results: dict, total: int) -> dict:
    schema_rate = results["schema_valid"] / total if total else 0.0
    direction_acc = (results["direction_correct"] / results["direction_total"]
                     if results["direction_total"] else None)
    refusal_rate = (results["refusal_correct"] / results["refusal_total"]
                    if results["refusal_total"] else None)
    low_c, low_t = results["buckets"]["low"]
    high_c, high_t = results["buckets"]["high"]
    low_acc = low_c / low_t if low_t else None
    high_acc = high_c / high_t if high_t else None
    calibration_ok = True
    if low_acc is not None and high_acc is not None:
        calibration_ok = high_acc >= low_acc

    print("=" * 60)
    print(f"schema 合法率      : {schema_rate:.1%}   （门槛 ≥ {GATES['schema_valid_rate']:.0%}）")
    if direction_acc is not None:
        print(f"方向准确率         : {direction_acc:.1%}  （对比基线，最多允许低 3pt）")
    else:
        print("方向准确率         : 无带标签用例")
    if refusal_rate is not None:
        print(f"拒答率（信息不足） : {refusal_rate:.1%}   （门槛 ≥ {GATES['refusal_rate']:.0%}）")
    print(f"校准（高桶 vs 低桶）: 高 {high_acc:.1%} / 低 {low_acc:.1%}"
          if low_acc is not None and high_acc is not None else "校准: 样本不足")
    print("=" * 60)
    print("逐 case 判定（评审用，别只看总分）：")
    for detail in results["details"]:
        mark = "✓" if detail.get("schema_valid") else "✗"
        extra = detail.get("predicted", detail.get("refused", ""))
        print(f"  {mark} {detail['id']} [{detail['kind']}] → {extra}")
    return {"schema_rate": schema_rate, "direction_acc": direction_acc,
            "refusal_rate": refusal_rate, "calibration_ok": calibration_ok}


def main() -> int:
    parser = argparse.ArgumentParser(description="领域金标集评估")
    parser.add_argument("--base", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--token", default=os.getenv("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--save", default=os.path.join(HERE, "eval_report.json"),
                        help="结果落盘路径（对比基线要用）")
    args = parser.parse_args()

    cases = load_cases()
    if not cases:
        print("✗ cases.yaml 为空——先人工审出一批金标（README §5）", file=sys.stderr)
        return 2
    # 拒收半成品草稿：draft_cases.py 的产物带空 expect_direction，
    # 混进评估会把方向准确率悄悄拉成 0（每条都判错）——那比报错危险
    unfilled = [c.get("id") for c in cases
                if c.get("kind", "direction") == "direction"
                and not str(c.get("expect_direction") or "").strip()]
    if unfilled:
        print(f"✗ 有 {len(unfilled)} 条方向型用例没填 expect_direction"
              f"（如 {unfilled[:3]}）——金标没审完不许评估", file=sys.stderr)
        return 2
    print(f"[*] 评估 {args.model} @ {args.base}，{len(cases)} 个金标用例")

    results = evaluate(args.base, args.model, args.token, cases)
    summary = report(results, len(cases))

    with io.open(args.save, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"model": args.model, "base": args.base, "summary": summary,
                   "details": results["details"]}, handle, ensure_ascii=False, indent=2)
    print(f"[*] 明细已写入 {args.save}")
    print("[*] 换入门槛：与基线报告对比四道门槛（README §5），全过才允许改 .env 切换")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
