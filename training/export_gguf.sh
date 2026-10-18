#!/bin/sh
# Converts the merged model of training/train.py to a quantized GGUF and an Ollama Modelfile.
#   sh training/export_gguf.sh [training/out] [Q4_K_M]
# Needs a llama.cpp checkout with its tools built (LLAMA_CPP=/path/to/llama.cpp, default ./llama.cpp):
#   git clone https://github.com/ggml-org/llama.cpp && cmake -S llama.cpp -B llama.cpp/build && cmake --build llama.cpp/build -j
#   pip install -r llama.cpp/requirements.txt
# Then: ollama create flymotion-trained -f training/out/gguf/Modelfile.trained, and run the benchmark on it.
set -eu

out=${1:-training/out}
quant=${2:-Q4_K_M}
llama=${LLAMA_CPP:-./llama.cpp}
die() { printf 'FlyMotion: %s\n' "$*" >&2; exit 1; }
[ -d "$out/merged" ] || die "no merged model in $out/merged: run training/train.py first."
[ -f "$llama/convert_hf_to_gguf.py" ] || die "llama.cpp not found at $llama (set LLAMA_CPP)."
quantize=$llama/build/bin/llama-quantize
[ -x "$quantize" ] || die "build llama.cpp first ($quantize is missing)."

mkdir -p "$out/gguf"
python3 "$llama/convert_hf_to_gguf.py" "$out/merged" --outfile "$out/gguf/flymotion-f16.gguf" --outtype f16
lower=$(printf '%s' "$quant" | tr 'A-Z' 'a-z')
"$quantize" "$out/gguf/flymotion-f16.gguf" "$out/gguf/flymotion-$lower.gguf" "$quant"
rm "$out/gguf/flymotion-f16.gguf"

here=$(cd "$(dirname "$0")/.." && pwd)
# Same settings and prompt as the published profile; only the weights change.
{
  printf '# FlyMotion fine-tuned on the FlyMotion dataset (training/train.py), %s.\n' "$quant"
  printf 'FROM ./flymotion-%s.gguf\nRENDERER qwen3.5\nPARSER qwen3.5\n' "$lower"
  sed -n '/^PARAMETER /p; /^SYSTEM """/,/"""$/p' "$here/Modelfile"
} > "$out/gguf/Modelfile.trained"
printf 'Wrote %s/gguf/flymotion-%s.gguf and %s/gguf/Modelfile.trained\n' "$out" "$lower" "$out"
