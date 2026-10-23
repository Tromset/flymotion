#!/bin/sh
# Publishes FlyMotion on ollama.com: <user>/flymotion0.2 and <user>/flymotion0.2lite.
#   sh scripts/publish-ollama.sh [<ollama.com username>] [<name> ...]
# Default user: Tromset. Default names: every model below. Example: sh scripts/publish-ollama.sh Tromset flymotion0.2lite
# Prerequisite: this machine's Ollama key is linked to the account (`ollama signin` once).
# GitHub Actions runs this script on every change to main (.github/workflows/publish-ollama.yml).
#   FLYMOTION_PRUNE=1  removes each model and its base after the push, to keep the disk free (used by CI)
set -eu

VERSION=0.2
# Modelfile, published name. Keep in sync with scripts/gen_modelfiles.py and docs/ollama/.
MODELS='Modelfile flymotion0.2
Modelfile.lite flymotion0.2lite'

die() { printf 'FlyMotion: %s\n' "$*" >&2; exit 1; }

user=${1:-Tromset}
[ "${user#-}" = "$user" ] || die "usage: sh scripts/publish-ollama.sh [<ollama.com username>] [<name> ...]"
[ $# -gt 0 ] && shift
command -v ollama >/dev/null 2>&1 || die "Ollama is not installed."
ollama list >/dev/null 2>&1 || die "Ollama is not responding. Start it with 'ollama serve'."

here=$(cd "$(dirname "$0")/.." && pwd)
python3 "$here/scripts/gen_modelfiles.py" --check || die "the Modelfiles are out of date."
wanted() { # wanted <name> [<requested name> ...]: true when nothing was requested or <name> was
  n=$1; shift
  [ $# -eq 0 ] && return 0
  for w in "$@"; do [ "$w" = "$n" ] && return 0; done
  return 1
}

echo "$MODELS" | while read -r file name; do
  wanted "$name" "$@" || continue
  base=$(sed -n 's/^FROM //p' "$here/$file" | head -n 1)
  [ -n "$base" ] || die "$file has no FROM line."
  printf '\n==> %s/%s (%s, base %s)\n' "$user" "$name" "$file" "$base"
  ollama pull "$base"
  ollama create "$user/$name" -f "$here/$file"
  ollama push "$user/$name"
  if [ "${FLYMOTION_PRUNE:-0}" = 1 ]; then ollama rm "$user/$name" "$base" >/dev/null; fi
done

printf '\nPublished FlyMotion %s. Anyone can now run:\n' "$VERSION"
printf '  ollama pull %s/flymotion0.2        (computers with 8 GB)\n' "$user"
printf '  ollama pull %s/flymotion0.2lite    (Raspberry Pi 5, 4 GB)\n' "$user"
