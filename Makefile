up:
	docker compose up --build

up-nb:
	docker compose up
	
down:
	docker compose down

indexer-trigger:
	docker compose run page-indexer-trigger

trigger-build:
	docker compose build page-indexer-trigger
