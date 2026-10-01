"""train_lora.py —— 股票资讯 LoRA 微调（LLaMA-Factory 包装器）。

⚠️ 这个脚本**必须**在有 GPU 与 LLaMA-Factory 的训练机上跑：
    pip install llamafactory>=0.9
    FORCE_GPU=1 python finetune/train_lora.py --dataset finetune/dataset

没有环境时它会**明确拒绝运行**而不是静默产出垃圾——训练脚本的失败模式
应该是"吵闹的拒绝"，不是"跑完发现学的是随机初始化"。

为什么包一层而不是直接给 llamafactory-cli 命令行：
把"本项目定死的超参与路径"（QLoRA 配置、dataset_info 注册、输出目录命名）
固定在代码里，训练机上只剩一个命令要记；调参改这里，不用翻 shell 历史。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 项目定死的超参（README §2 的"任务/数据量"结论落在这里）：
# - 4bit QLoRA：消费级 24G 显存可训 8B；有钱上 A100 就把 quant bits 去掉全参 LoRA
# - 学习率刻意低（1e-4 是 LLaMA-Factory 默认，我们再压一档）：
#   蒸馏任务最容易过拟合——模型学会"复述训练集里的那句话"而不是"判新资讯"
# - 早停看 eval_loss，3 epoch 上限
LORA_CONFIG = {
    "stage": "sft",
    "do_train": True,
    "model_name_or_path": "Qwen/Qwen3-8B-Instruct",
    "dataset": "stock_news",
    "dataset_dir": os.path.join(HERE, "llamafactory_data"),
    "template": "qwen",
    "finetuning_type": "lora",
    "lora_target": "all",
    "lora_rank": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "quantization_bit": 4,
    "output_dir": os.path.join(HERE, "adapters"),
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 8,
    "learning_rate": 5.0e-5,
    "num_train_epochs": 3.0,
    "lr_scheduler_type": "cosine",
    "warmup_ratio": 0.05,
    "bf16": True,
    "logging_steps": 10,
    "save_steps": 200,
    "eval_steps": 200,
    "per_device_eval_batch_size": 4,
    "eval_strategy": "steps",
    "load_best_model_at_end": True,
}

DATASET_INFO_ENTRY = {
    "file_name": "../dataset/train.jsonl",
    "formatting": "sharegpt",
    "columns": {"messages": "conversations"},
}


def register_dataset() -> None:
    """把 dataset/ 注册进 LLaMA-Factory 的 dataset_info.json（幂等）。"""
    data_dir = os.path.join(HERE, "llamafactory_data")
    os.makedirs(data_dir, exist_ok=True)
    info_path = os.path.join(data_dir, "dataset_info.json")
    info = {}
    if os.path.exists(info_path):
        with open(info_path, encoding="utf-8") as handle:
            try:
                info = json.load(handle)
            except json.JSONDecodeError:
                info = {}
    info["stock_news"] = dict(DATASET_INFO_ENTRY)
    with open(info_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(info, handle, ensure_ascii=False, indent=2)


def preflight() -> list[str]:
    """训练前检查：每一条不过都不许开跑。"""
    problems = []
    train_path = os.path.join(HERE, "dataset", "train.jsonl")
    if not os.path.exists(train_path):
        problems.append(f"缺 {train_path} —— 先跑 build_dataset.py")
    if shutil.which("llamafactory") is None and shutil.which("llamafactory-cli") is None:
        problems.append("未找到 llamafactory / llamafactory-cli —— pip install llamafactory>=0.9")
    if os.getenv("FORCE_GPU") != "1":
        problems.append("确认过训练机有 GPU 后设 FORCE_GPU=1（防手滑在开发机上跑）")
    try:
        import torch  # noqa: PLC0415
        if not torch.cuda.is_available():
            problems.append("torch.cuda 不可用 —— QLoRA 需要 GPU")
    except ImportError:
        problems.append("未安装 torch（应由 llamafactory 依赖带入）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="股票资讯 LoRA 微调（LLaMA-Factory）")
    parser.add_argument("--model", default=LORA_CONFIG["model_name_or_path"],
                        help="基座模型（默认 Qwen3-8B-Instruct，见 README §1 选型）")
    parser.add_argument("--output", default=None, help="适配器输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印配置不训练")
    args = parser.parse_args()

    config = dict(LORA_CONFIG)
    config["model_name_or_path"] = args.model
    if args.output:
        config["output_dir"] = args.output

    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0

    problems = preflight()
    if problems:
        print("✗ 训练前置检查未通过：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    register_dataset()
    cli = shutil.which("llamafactory-cli") or shutil.which("llamafactory")
    print(f"[*] 用 {cli} 训练，输出 → {config['output_dir']}")
    print("[*] 训练完成后：README §3 部署（vLLM --enable-lora）→ eval_domain.py 过门槛才许换入")
    result = subprocess.run([cli, "train", json.dumps(config, ensure_ascii=False)],
                            check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
