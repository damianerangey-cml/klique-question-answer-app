#!/bin/sh
# Fills the chat model's address and token into the guardrails config, then serves it on port 8000.
set -e
mkdir -p /app/config/support
python - <<'PY'
import os
text = open('/app/config.tmpl.yml').read()
text = text.replace('${CHAT_URL}', os.environ['CHAT_URL']).replace('${ROUTER_TOKEN}', os.environ['ROUTER_TOKEN'])
open('/app/config/support/config.yml', 'w').write(text)
PY
exec nemoguardrails server --config /app/config --port 8000
