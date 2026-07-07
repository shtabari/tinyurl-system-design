COMPOSE_FILE = deploy/compose/docker-compose.yml

.PHONY: up down down-v restart logs test migrate shell

up:
	docker compose -f $(COMPOSE_FILE) up --build -d
down:
	docker compose -f $(COMPOSE_FILE) down
down-v:                       # destroys the postgres volume — use deliberately
	docker compose -f $(COMPOSE_FILE) down -v
restart:                      # bounces api WITHOUT rebuilding (runs old image)
	docker compose -f $(COMPOSE_FILE) restart api
logs:
	docker compose -f $(COMPOSE_FILE) logs -f api
migrate:                      # works after Task 3 (needs alembic + migrations/ in image)
	docker compose -f $(COMPOSE_FILE) exec api alembic upgrade head
test:                         # path/copy fix needed when tests land (Task 4+)
	docker compose -f $(COMPOSE_FILE) exec api pytest tests -v
shell:
	docker compose -f $(COMPOSE_FILE) exec api /bin/bash
