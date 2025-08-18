# Makefile - Comandos úteis para construir e rodar os contêineres
build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f rag-api

ingest-dir:
	# Executa contêiner de ingestão para processar PDFs em ./pdfs
	docker-compose run --rm rag-api python ingest.py /data/pdfs

# Atalho para executar ingestão via API (requer curl instalado no host)
ingest-api:
ifneq ("$(file)","")
	curl -X POST -H "Content-Type: multipart/form-data" \
	     -F "files=@$(file)" "http://localhost:8000/ingest"
else
	@echo "Uso: make ingest-api file=<caminho_do_pdf>"
endif

query:
	@echo "Uso: curl -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' -d '{\"q\": \"<PERGUNTA>\", \"top_k\":3}' http://localhost:8000/query"
