# Deploy the remote WhatsApp MCP server on this host (systemd + Cloudflare Tunnel).
# Run from the deploy checkout: make -C deploy <target>
REPO    := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
USER_   := $(shell id -un)
HOME_   := $(HOME)
GO      ?= $(shell command -v go || echo /usr/local/go/bin/go)
UV      ?= $(shell command -v uv || echo $(HOME)/.local/bin/uv)
ENVFILE := $(HOME_)/.config/whatsapp-mcp.env
UNITS   := whatsapp-bridge whatsapp-mcp-server
SED     := sed -e 's|__USER__|$(USER_)|g' -e 's|__HOME__|$(HOME_)|g' -e 's|__REPO__|$(REPO)|g'

.PHONY: help build test install deploy update restart status logs qr

help:
	@echo "make build    build the bridge, sync the Python venv (frozen lock)"
	@echo "make test     go test + pytest"
	@echo "make install  env file (if missing), DATA_DIR, systemd units (sudo)"
	@echo "make deploy   git pull --ff-only, build, install, restart, status"
	@echo "make update   build + restart (no pull)"
	@echo "make restart | status | logs"

build:
	cd $(REPO)/whatsapp-bridge && CGO_ENABLED=1 $(GO) build -trimpath -o whatsapp-bridge .
	cd $(REPO)/whatsapp-mcp-server && $(UV) sync --frozen --no-dev -q

test:
	cd $(REPO)/whatsapp-bridge && CGO_ENABLED=1 $(GO) test ./...
	cd $(REPO)/whatsapp-mcp-server && $(UV) run --extra dev pytest -q

install:
	@test -f $(ENVFILE) || (install -m 600 $(REPO)/deploy/env.example $(ENVFILE) && echo "created $(ENVFILE)")
	@. $(ENVFILE) && install -d -m 700 "$$DATA_DIR" "$$DATA_DIR/outbox"
	for u in $(UNITS); do $(SED) $(REPO)/deploy/systemd/$$u.service | sudo tee /etc/systemd/system/$$u.service >/dev/null; done
	sudo systemctl daemon-reload
	sudo systemctl enable $(UNITS:%=%.service)

restart:
	sudo systemctl restart $(UNITS:%=%.service)

status:
	@systemctl --no-pager --lines=0 status $(UNITS:%=%.service) local-tunnel.service || true
	@. $(ENVFILE) && curl -fsS "http://127.0.0.1:$$WHATSAPP_MCP_PORT$$BASE_PATH/healthz" && echo

logs:
	journalctl -u whatsapp-bridge -u whatsapp-mcp-server -n 100 --no-pager

update: build restart status

deploy:
	cd $(REPO) && git pull --ff-only
	$(MAKE) -C $(REPO)/deploy build install restart status
