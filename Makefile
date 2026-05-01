.PHONY: install preflight help

## install: Run preflight checks then pull and start the containers.
install:
	./bin/preflight.sh && docker compose pull && docker compose up -d

## preflight: Run host state checks without starting anything.
preflight:
	./bin/preflight.sh

## help: List available targets.
help:
	@grep -E '^## ' Makefile | sed 's/^## //'
