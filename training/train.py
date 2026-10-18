#!/usr/bin/env python3
"""LoRA fine-tuning of a Qwen3.5 base on the FlyMotion dataset (path to FlyMotion 0.2: the plan without the long prompt).

  pip install -r training/requirements.txt
  python3 training/build_dataset.py
  python3 training/train.py --base Qwen/Qwen3.5-2B [--epochs 2] [--out training/out]
  sh training/export_gguf.sh training/out

Runs on a CUDA GPU (a free Colab T4 is enough for the 2B), on Apple Silicon (MPS, slower) or, very slowly, on CPU.
The prompt is rendered exactly as Ollama renders it with thinking off, and the loss is computed on the plan only.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path, tokenizer):
    rows = []
    for line in Path(path).read_text().splitlines():
        system, user, assistant = json.loads(line)['messages']
        prompt = tokenizer.apply_chat_template([system, user], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        rows.append({'prompt': prompt, 'completion': assistant['content'] + tokenizer.eos_token})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='Qwen/Qwen3.5-2B')
    parser.add_argument('--data', default=str(ROOT / 'data/flymotion-commands'))
    parser.add_argument('--out', default=str(ROOT / 'training/out'))
    parser.add_argument('--epochs', type=float, default=2)
    parser.add_argument('--rank', type=int, default=16)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--batch', type=int, default=4)
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    train = Dataset.from_list(load(Path(args.data) / 'train.jsonl', tokenizer))
    valid = Dataset.from_list(load(Path(args.data) / 'valid.jsonl', tokenizer))
    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=dtype)
    config = SFTConfig(
        output_dir=str(Path(args.out) / 'checkpoints'), num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=args.batch, gradient_accumulation_steps=max(1, 16 // args.batch),
        lr_scheduler_type='cosine', warmup_ratio=0.05, logging_steps=10, eval_strategy='steps', eval_steps=100,
        save_strategy='steps', save_steps=100, save_total_limit=2, load_best_model_at_end=True,
        completion_only_loss=True, max_length=2048, bf16=dtype == torch.bfloat16, gradient_checkpointing=True,
        report_to='none', seed=20261018)
    lora = LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, target_modules='all-linear', task_type='CAUSAL_LM')
    trainer = SFTTrainer(model=model, args=config, train_dataset=train, eval_dataset=valid, processing_class=tokenizer, peft_config=lora)
    trainer.train()
    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(Path(args.out) / 'merged', safe_serialization=True)
    tokenizer.save_pretrained(Path(args.out) / 'merged')
    print(f'Merged model in {Path(args.out) / "merged"}. Next: sh training/export_gguf.sh {args.out}')


if __name__ == '__main__':
    main()
