up:
	docker compose up --build

up-nb:
	docker compose up

down:
	docker compose down

indexer-trigger:
	docker compose --profile trigger run --rm page-indexer-trigger

indexer-trigger-build:
	docker compose build page-indexer-trigger

processor-trigger:
	docker compose --profile trigger run --rm page-processor-trigger

processor-trigger-build:
	docker compose build page-processor-trigger

logs:
	docker compose logs -f
