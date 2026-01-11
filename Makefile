up:
	docker compose up --build

down:
	docker compose down

indexer-trigger:
	docker compose run page-indexer-trigger
