.PHONY: up down logs build test clean

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

test:
	cd backend && pip install -r requirements.txt && pytest tests/ -v

clean:
	docker compose down -v
	docker system prune -f

backend-shell:
	docker compose exec backend bash

frontend-shell:
	docker compose exec frontend sh
