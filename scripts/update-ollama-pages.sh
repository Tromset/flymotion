#!/bin/sh
# Updates the model pages on ollama.com from docs/ollama/<model>.md.
#   OLLAMA_API_KEY=... sh scripts/update-ollama-pages.sh [<ollama.com username>]
# Uses the same request as the page's Edit button (POST https://ollama.com/<user>/<model>, field readme),
# authenticated with an ollama.com API key (https://ollama.com/settings/keys).
# A page that cannot be updated is reported, and the script exits with status 1 at the end.
set -eu

user=${1:-Tromset}
[ -n "${OLLAMA_API_KEY:-}" ] || { printf 'FlyMotion: OLLAMA_API_KEY is not set.\n' >&2; exit 1; }
here=$(cd "$(dirname "$0")/.." && pwd)

failed=0
for page in "$here"/docs/ollama/*.md; do
  model=$(basename "$page" .md)
  # No redirect following: a redirect to the sign-in page means the key was refused.
  status=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "https://ollama.com/$user/$model" \
    -H "Authorization: Bearer $OLLAMA_API_KEY" --data-urlencode "readme@$page") || status=000
  case $status in
    2??) printf 'Updated https://ollama.com/%s/%s\n' "$user" "$model" ;;
    *) printf 'FlyMotion: could not update https://ollama.com/%s/%s (HTTP %s)\n' "$user" "$model" "$status" >&2; failed=1 ;;
  esac
done
exit "$failed"
