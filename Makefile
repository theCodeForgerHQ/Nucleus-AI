NAMESPACE := nucleus-ai

.PHONY: \
	docker-up docker-up-nb docker-down docker-logs \
	indexer-trigger processor-trigger \
	k8s-up k8s-down k8s-watch k8s-status k8s-clean

# --------------------
# Docker Compose
# --------------------

docker-up:
	docker compose up --build

docker-up-nb:
	docker compose up

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

indexer-trigger:
	docker compose --profile trigger run --rm page-indexer-trigger

processor-trigger:
	docker compose --profile trigger run --rm page-processor-trigger


# --------------------
# Kubernetes
# --------------------

k8s-up:
	kubectl apply -f k8s/ -n $(NAMESPACE)

k8s-down:
	kubectl scale deployment --all -n $(NAMESPACE) --replicas=0

k8s-watch:
	kubectl get pods -n $(NAMESPACE) -w

k8s-status:
	kubectl get pods -n $(NAMESPACE)
	kubectl get svc -n $(NAMESPACE)

k8s-load-env:
	kubectl create configmap nucleus-ai-env --from-env-file=.env -n nucleus-ai

k8s-clean:
	kubectl delete namespace $(NAMESPACE)
