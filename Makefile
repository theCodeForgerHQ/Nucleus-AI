up:
	docker compose up --build

up-nb:
	docker compose up

down:
	docker compose down

indexer-trigger:
	docker compose --profile trigger run page-indexer-trigger

indexer-trigger-build:
	docker compose build page-indexer-trigger

processor-trigger:
	docker compose --profile trigger run content-processor-trigger

processor-trigger-build:
	docker compose build content-processor-trigger

embedder:
	docker compose up --build llama-text-embedder

embedder-nb:
	docker compose up llama-text-embedder

embedder-down:
	docker compose stop llama-text-embedder

