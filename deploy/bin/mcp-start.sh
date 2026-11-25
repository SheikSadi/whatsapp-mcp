#!/usr/bin/env bash
# Start the remote WhatsApp MCP server with its bearer token from SSM Parameter
# Store (SecureString). On the very first start the token does not exist yet:
# generate one and store it without --overwrite, so two racing starts agree.
# The token is never written to the repo, the env file, or a command line.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PARAM="${MCP_TOKEN_PARAM:-/whatsapp-mcp/auth-token}"
export AWS_DEFAULT_REGION="${AWS_REGION:-ap-southeast-2}"
# Use the EC2 instance role only; an interactive `aws login` session in ~/.aws expires.
export AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null

get_token() {
  aws ssm get-parameter --name "$PARAM" --with-decryption --query Parameter.Value --output text
}

if ! token="$(get_token 2>/dev/null)"; then
  echo "mcp-start: $PARAM not readable; creating it (first start)" >&2
  tmp="$(mktemp)"            # 0600 under UMask=0077
  trap 'rm -f "$tmp"' EXIT
  openssl rand -hex 32 | tr -d '\n' >"$tmp"
  aws ssm put-parameter --name "$PARAM" --type SecureString --value "file://$tmp" \
    --description "Bearer token for the WhatsApp MCP server" --no-overwrite >/dev/null || true
  rm -f "$tmp"
  token="$(get_token)"
fi

export MCP_AUTH_TOKEN="$token"
cd "$REPO/whatsapp-mcp-server"
exec "$REPO/whatsapp-mcp-server/.venv/bin/python" remote.py
