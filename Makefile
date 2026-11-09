.PHONY: all init build logs shell run stop deploy help

all: help

uid := $(shell id -u)
gid := $(shell id -g)

PROJECT_NAME := blog

# Untracked local settings, e.g. the deploy target:
#   REMOTE_SSH ?= user@example.com
#   REMOTE_APP_DIR ?= /srv/flow
-include local.mk
REMOTE_SSH ?=
REMOTE_APP_DIR ?=

build:
	@echo "Building blog image..."
	uid=$(uid) gid=$(gid) docker compose -p $(PROJECT_NAME) build

init:
	@echo "Ensuring local data directories exist..."
	@mkdir -p content/p upload build cache

logs:
	@docker logs blog

shell:
	@docker exec -it blog /bin/bash

run:
	@echo "Starting blog..."
	$(MAKE) init
	$(MAKE) stop
	@if [ -z "$$(docker images -q blog)" ]; then \
		echo "Image not found. Building..."; \
		$(MAKE) build; \
	fi
	docker compose -p $(PROJECT_NAME) up -d

stop:
	@docker compose -p $(PROJECT_NAME) down

deploy:
	@if [ -z "$(REMOTE_SSH)" ] || [ -z "$(REMOTE_APP_DIR)" ]; then \
		echo "deploy needs REMOTE_SSH and REMOTE_APP_DIR (set them in local.mk)"; \
		exit 1; \
	fi
	@ssh $(REMOTE_SSH) "source /etc/profile && cd $(REMOTE_APP_DIR) && $(MAKE) build && $(MAKE) run"

help:
	@echo '===================='
	@echo '    BLOG - MAKEFILE  '
	@echo '===================='
	@echo ''
	@echo 'Build Commands:'
	@echo '  build               - Build production image'
	@echo ''
	@echo 'Run Commands:'
	@echo '  run                 - Run in production mode'
	@echo ''
	@echo 'Logging Commands:'
	@echo '  logs                - Show blog logs'
	@echo ''
	@echo 'Shell Access:'
	@echo '  shell               - Access blog container shell'
	@echo ''
	@echo 'Maintenance Commands:'
	@echo '  stop                - Stop the blog container'
	@echo '  deploy              - Build and run on REMOTE_SSH (set in local.mk)'
	@echo '===================='
