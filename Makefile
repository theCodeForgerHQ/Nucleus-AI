.PHONY: \
	up up-nb down logs \
	indexer-trigger processor-oneoff \
	k8s-up k8s-down k8s-watch k8s-status k8s-clean

up:
	docker compose up --build

up-nb:
	docker compose up

down:
	docker compose down

logs:
	docker compose logs -f

indexer-trigger:
	docker compose --profile trigger run --rm page-indexer-trigger

processor-oneoff:
	docker compose --profile trigger run --rm page-processor-oneoff
