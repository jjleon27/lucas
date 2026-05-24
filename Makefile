# LUCAS — atajos de dev.
# Corre `make` (sin argumentos) para ver los comandos disponibles.

.DEFAULT_GOAL := help
.PHONY: help up down restart reset rebuild logs backend-logs frontend-logs status shell-backend shell-db usage

help:  ## Muestra esta ayuda
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up:  ## Levanta todo (backend + frontend + db)
	docker compose up -d --build

down:  ## Apaga todo (conserva la DB)
	docker compose down

restart:  ## Reinicia sin rebuild (tras cambiar .env)
	docker compose restart

rebuild:  ## Rebuild sin cache (tras cambiar requirements.txt o package.json)
	docker compose build --no-cache
	docker compose up -d

reset:  ## 💣 Borra la DB completa y rearranca limpio (arregla tokens viejos)
	docker compose down -v
	docker compose up -d --build
	@echo ""
	@echo "✅ Todo limpio. Ahora:"
	@echo "   1. Entra a http://localhost:3000"
	@echo "   2. Si la pestaña estaba abierta, refréscala (Cmd+Shift+R)"
	@echo "   3. Vuelve a loguear con tu email"

logs:  ## Logs en vivo del backend (Ctrl+C para salir)
	docker compose logs -f backend

backend-logs:  ## Últimos 80 logs del backend
	docker compose logs backend --tail 80

frontend-logs:  ## Últimos 80 logs del frontend
	docker compose logs frontend --tail 80

status:  ## Verifica que la IA esté viva
	@echo "→ /health"
	@curl -s http://localhost:8000/health || echo "  backend caído"
	@echo ""
	@echo "→ /ai/status"
	@curl -s http://localhost:8000/ai/status || echo "  backend caído"
	@echo ""

shell-backend:  ## Entra al contenedor del backend (debug)
	docker compose exec backend bash

shell-db:  ## Entra a psql
	docker compose exec db psql -U lucas -d lucas
