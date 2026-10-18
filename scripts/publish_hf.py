#!/usr/bin/env python3
"""Publishes FlyMotion on the Hugging Face Hub.

  HF_TOKEN=... python3 scripts/publish_hf.py [--owner Tromset] [--what dataset,model] [--weights training/out/gguf]

- dataset  <owner>/flymotion-commands: train.jsonl and valid.jsonl from training/build_dataset.py, with docs/hf/dataset.md
- model    <owner>/flymotion: the Modelfiles, the system prompt, the JSON schema, the robot profiles, the benchmark
           results and docs/hf/model.md; with --weights, the fine-tuned GGUF files of that folder too.
Needs `pip install huggingface_hub` and a write token (https://huggingface.co/settings/tokens).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flymotion.plan import load_profile, schema  # noqa: E402
from flymotion.prompt import system_prompt  # noqa: E402


def stage_dataset(folder):
    subprocess.run([sys.executable, str(ROOT / 'training/build_dataset.py'), '--out', str(folder / 'data')], check=True)
    shutil.copy(ROOT / 'docs/hf/dataset.md', folder / 'README.md')


def stage_model(folder, weights):
    profile = load_profile('microduck')
    shutil.copy(ROOT / 'docs/hf/model.md', folder / 'README.md')
    for name in ('Modelfile', 'Modelfile.lite'):
        shutil.copy(ROOT / name, folder / name)
    (folder / 'system_prompt.txt').write_text(system_prompt(profile) + '\n')
    (folder / 'plan.schema.json').write_text(json.dumps(schema(profile), indent=1) + '\n')
    shutil.copytree(ROOT / 'robots', folder / 'robots')
    if (ROOT / 'docs/bench').is_dir():
        shutil.copytree(ROOT / 'docs/bench', folder / 'bench')
    if weights:
        for file in Path(weights).iterdir():
            if file.suffix in ('.gguf',) or file.name == 'Modelfile.trained':
                shutil.copy(file, folder / file.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', default='Tromset')
    parser.add_argument('--what', default='dataset,model')
    parser.add_argument('--weights', help='folder with the fine-tuned GGUF files (training/export_gguf.sh)')
    parser.add_argument('--dry-run', action='store_true', help='stage the files and print them, upload nothing')
    args = parser.parse_args()
    token = os.environ.get('HF_TOKEN')
    if not token and not args.dry_run:
        sys.exit('FlyMotion: HF_TOKEN is not set (a write token from https://huggingface.co/settings/tokens).')
    targets = {'dataset': (f'{args.owner}/flymotion-commands', 'dataset', lambda f: stage_dataset(f)),
               'model': (f'{args.owner}/flymotion', 'model', lambda f: stage_model(f, args.weights))}
    for what in args.what.split(','):
        repo, kind, stage = targets[what]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stage(folder)
            files = sorted(str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file())
            print(f'{repo} ({kind}): {len(files)} files')
            if args.dry_run:
                print('  ' + '\n  '.join(files))
                continue
            from huggingface_hub import HfApi
            api = HfApi(token=token)
            api.create_repo(repo, repo_type=kind, exist_ok=True)
            api.upload_folder(folder_path=folder, repo_id=repo, repo_type=kind, commit_message='Publish FlyMotion from GitHub')
            print(f'  https://huggingface.co/{"datasets/" if kind == "dataset" else ""}{repo}')


if __name__ == '__main__':
    main()
