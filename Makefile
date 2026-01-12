up:
	docker compose up --build

up-nb:
	docker compose up
	
down:
	docker compose down

indexer-trigger:
	docker compose run page-indexer-trigger

indexer-trigger-build:
	docker compose build page-indexer-trigger

content-processor-trigger:
	docker compose run content-processor-trigger

content-processor-trigger-build:
	docker compose build content-processor-trigger
