# app.py
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import re, unicodedata
import requests
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from settings import settings
from ingest import ingest_pdf, qdrant, COLLECTION_NAME, qmodels, embed_text
from siepe_worker import processar_todos, processar_url
import os
import json

# --------------------------------------------
# Configuração / logging
# --------------------------------------------
logger = logging.getLogger("rag_api")
logger.setLevel(logging.INFO)

app = FastAPI(title="RAG API", version="1.0")

# CORS 
if settings.enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --------------------------------------------
# Auth via Bearer <RAG_TOKEN>
# --------------------------------------------
def verify_token(authorization: str = Header(None)):
    token = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not token or token != settings.rag_token:
        logger.warning("Acesso não autorizado - token inválido.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# --------------------------------------------
# Modelos
# --------------------------------------------
class SiepeIngestRequest(BaseModel):
    anos: Optional[List[str]] = None
    areas: Optional[List[str]] = None     
    eventos: Optional[List[str]] = None    
    max_itens_por_pagina: Optional[int] = None
    somente_esta_pagina: Optional[dict] = None  

class ChatMessage(BaseModel):
    role: str
    content: str

class QueryRequest(BaseModel):
    q: str
    top_k: int = 3
    filters: dict | None = None
    hybrid: bool = False 

# --------------------------------------------
# Ingest job infra 
# --------------------------------------------
AREAS_MAP = {
    "ca": "Ciências Agrárias", "cb": "Ciências Biológicas", "ce": "Ciências Exatas e da Terra",
    "ch": "Ciências Humanas", "cs": "Ciências da Saúde", "sa": "Ciências Sociais Aplicadas",
    "en": "Engenharias", "la": "Linguística, Letras e Artes", "md": "Multidisciplinar",
    "G1": "Diversidade no Ensino Superior", "G2": "Tecnologias Educacionais na Educação Superior",
    "G3": "Projetos e Programas Institucionais", "G4": "Monitorias", "G5": "Relato de experiência na Graduação",
}
EVENTOS_MAP = {
    "ceg": "Congresso de Ensino de Graduação", "cic": "Congresso de Iniciação Científica",
    "cit": "Congresso de Inovação Tecnológica", "enpos": "Encontro de Pós Graduação",
}

JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()

def extract_filters_and_refine_query(original_query: str) -> Dict[str, Any]:
    """
    Usa um LLM para extrair filtros estruturados de uma pergunta em linguagem natural
    e refinar a query para a busca vetorial.
    """
    # Este prompt instrui o LLM a atuar como um "tradutor" de linguagem natural para JSON.
    # É uma tarefa de "Function Calling" ou "Tool Use" simplificada.
    prompt = f"""
Você é um assistente de API que extrai informações de uma pergunta para uma busca em um banco de dados de artigos científicos.
A partir da pergunta do usuário, extraia os valores para os seguintes campos de filtro: 'titulo', 'autores', 'orientador', 'ano', 'evento', 'area'.
Além disso, crie uma 'query_refinada' que contenha o tópico principal da pergunta, removendo as informações que já foram extraídas para os filtros.
Se um campo não for mencionado na pergunta, não o inclua no JSON. O 'ano' deve ser uma string de 4 dígitos.

Pergunta do usuário: "{original_query}"

Responda APENAS com um objeto JSON válido contendo as chaves "filters" e "query_refinada".

Exemplo 1:
Pergunta do usuário: "Quais artigos do orientador Nome Ficticio sobre tema Y foram publicados em 2015?"
JSON:
{{
  "filters": {{
    "orientador": "Nome Ficticio",
    "ano": "2015"
  }},
  "query_refinada": "artigos sobre tema Y"
}}

Exemplo 2:
Pergunta do usuário: "me mostre trabalhos da área de engenharias"
JSON:
{{
  "filters": {{
    "area": "Engenharias"
  }},
  "query_refinada": "trabalhos da área de engenharias"
}}

Exemplo 3:
Pergunta do usuário: "o que é inteligência artificial?"
JSON:
{{
  "filters": {{}},
  "query_refinada": "o que é inteligência artificial?"
}}

Agora, processe a pergunta real.

Pergunta do usuário: "{original_query}"
JSON:
"""

    try:
        model_name = getattr(settings, "llama_model_name", None) or os.path.basename(
            os.environ.get("LLAMA_MODEL_PATH", "local")
        )
        url = f"{settings.llama_api_base}/completions"
        payload = {
            "model": model_name,
            "prompt": prompt,
            "max_tokens": 256, 
            "temperature": 0.0,
            "stop": ["\n\n", "Pergunta do usuário:"]
        }
        headers = {}
        if settings.llama_api_key:
            headers["Authorization"] = f"Bearer {settings.llama_api_key}"

        response = requests.post(url, json=payload, headers=headers, timeout=600)
        response.raise_for_status()
        data = response.json()

        text_response = ""
        if "choices" in data and data["choices"]:
            ch = data["choices"][0]
            text_response = (ch.get("text") or ch.get("message", {}).get("content") or "").strip()
        else:
            text_response = (data.get("content") or "").strip()
        logger.info("="*50)
        logger.info("RAW RESPONSE FROM LLM:")
        logger.info(text_response)
        logger.info("="*50)
        parsed_json = json.JSONDecoder().raw_decode(text_response)[0]
        
        if "filters" in parsed_json and "query_refinada" in parsed_json:
            logger.info(f"Filtros extraídos: {parsed_json['filters']}")
            logger.info(f"Query refinada: {parsed_json['query_refinada']}")
            return parsed_json
        else:
            raise ValueError("JSON retornado não contém as chaves esperadas.")

    except Exception as e:
        logger.error(f"Falha ao extrair filtros com LLM: {e}. Usando query original sem filtros.")
        return {
            "filters": {},
            "query_refinada": original_query
        }

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _update_job(job_id: str, patch: Dict[str, Any]):
    with JOB_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(patch)
            JOBS[job_id]["updated_at"] = _now_iso()

def _progress_cb_factory(job_id: str):
    def cb(evt: Dict[str, Any]):
        e = evt.get("event")
        if e == "page_start":
            _update_job(job_id, {
                "current_page": {
                    "ano": evt["ano"], "area_code": evt["area_code"], "area_nome": evt["area_nome"],
                    "evento_code": evt["evento_code"], "evento_nome": evt["evento_nome"],
                    "url": evt["url"], "total": evt["total"], "started": True
                }
            })
        elif e == "item_start":
            _update_job(job_id, {
                "last_item": {
                    "status": "processing",
                    "ano": evt["ano"], "area_code": evt["area_code"], "area_nome": evt["area_nome"],
                    "evento_code": evt["evento_code"], "evento_nome": evt["evento_nome"],
                    "titulo": evt["titulo"], "link_pdf": evt["link_pdf"],
                    "idx": evt["idx"], "total": evt["total"]
                }
            })
        elif e == "item_done":
            with JOB_LOCK:
                st = JOBS.get(job_id, {})
                prog = st.setdefault("counters", {"ok": 0, "falha": 0})
                if evt.get("status") == "ok":
                    prog["ok"] += 1
                    st["last_ok_item"] = {
                        "ano": evt["ano"], "area_code": evt["area_code"], "area_nome": evt["area_nome"],
                        "evento_code": evt["evento_code"], "evento_nome": evt["evento_nome"],
                        "titulo": evt["titulo"], "link_pdf": evt["link_pdf"],
                        "idx": evt["idx"], "total": evt["total"]
                    }
                else:
                    prog["falha"] += 1
                    st.setdefault("errors", []).append({
                        "ano": evt["ano"], "area_code": evt["area_code"], "evento_code": evt["evento_code"],
                        "titulo": evt["titulo"], "msg": evt.get("error", "")
                    })
                st["last_item"] = {
                    "status": evt.get("status"),
                    "ano": evt["ano"], "area_code": evt["area_code"], "area_nome": evt["area_nome"],
                    "evento_code": evt["evento_code"], "evento_nome": evt["evento_nome"],
                    "titulo": evt["titulo"], "link_pdf": evt["link_pdf"],
                    "idx": evt["idx"], "total": evt["total"]
                }
                st["updated_at"] = _now_iso()
                JOBS[job_id] = st
        elif e == "page_done":
            with JOB_LOCK:
                st = JOBS.get(job_id, {})
                pages = st.setdefault("pages", {"done": 0})
                pages["done"] += 1
                st["updated_at"] = _now_iso()
                JOBS[job_id] = st
    return cb

def _build_area_list(codes: Optional[List[str]]):
    if not codes:
        return None
    return [(c, AREAS_MAP.get(c, c)) for c in codes]

def _build_event_list(codes: Optional[List[str]]):
    if not codes:
        return None
    return [(c, EVENTOS_MAP.get(c, c)) for c in codes]

def _run_job(job_id: str, req: SiepeIngestRequest):
    try:
        _update_job(job_id, {"status": "running"})
        cb = _progress_cb_factory(job_id)

        if req.somente_esta_pagina:
            ano = req.somente_esta_pagina.get("ano")
            area_code = req.somente_esta_pagina.get("area")
            evento_code = req.somente_esta_pagina.get("evento")
            area_nome = AREAS_MAP.get(area_code, area_code)
            evento_nome = EVENTOS_MAP.get(evento_code, evento_code)
            resumo = processar_url(
                ano=ano, area_code=area_code, area_nome=area_nome,
                evento_code=evento_code, evento_nome=evento_nome,
                max_itens=req.max_itens_por_pagina, on_item=cb
            )
        else:
            resumo = processar_todos(
                anos=req.anos or None,
                areas=_build_area_list(req.areas),
                eventos=_build_event_list(req.eventos),
                max_itens_por_pagina=req.max_itens_por_pagina,
                on_item=cb
            )
        _update_job(job_id, {"status": "done", "result": resumo})
    except Exception as e:
        _update_job(job_id, {"status": "error", "error": str(e)})

# --------------------------------------------
# Rotas SIEPE ingest (background job)
# --------------------------------------------
@app.post("/siepe/ingest/start", dependencies=[Depends(verify_token)])
def siepe_start(req: SiepeIngestRequest):
    job_id = str(uuid.uuid4())
    with JOB_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "params": req.model_dump(),
            "counters": {"ok": 0, "falha": 0},
            "pages": {"done": 0},
        }
    t = threading.Thread(target=_run_job, args=(job_id, req), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.get("/siepe/ingest/status/{job_id}", dependencies=[Depends(verify_token)])
def siepe_status(job_id: str):
    st = JOBS.get(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="job_id não encontrado")
    return {
        "job_id": job_id,
        "status": st.get("status"),
        "created_at": st.get("created_at"),
        "updated_at": st.get("updated_at"),
        "counters": st.get("counters"),
        "pages": st.get("pages"),
        "current_page": st.get("current_page"),
        "last_item": st.get("last_item"),
        "last_ok_item": st.get("last_ok_item"),
        "error": st.get("error"),
    }

# --------------------------------------------
# Ingest de PDFs enviados
# --------------------------------------------
@app.post("/ingest")
async def ingest_endpoint(files: List[UploadFile] = File(...)):
    results = []
    for upload in files:
        if upload.content_type not in ["application/pdf", "application/octet-stream"]:
            raise HTTPException(status_code=400, detail=f"Arquivo {upload.filename} não é PDF.")
        data = await upload.read()
        tmp_path = f"/tmp/{upload.filename}"
        with open(tmp_path, "wb") as f:
            f.write(data)
        try:
            ingest_pdf(tmp_path)
            results.append({"filename": upload.filename, "status": "success"})
        except Exception as e:
            logger.error(f"Erro ao ingerir {upload.filename}: {e}")
            results.append({"filename": upload.filename, "status": f"error: {str(e)}"})
    return {"ingested": results}

# --------------------------------------------
# Query 
# --------------------------------------------
@app.post("/query", dependencies=[Depends(verify_token)])
def query_endpoint(request: QueryRequest):
    q = (request.q or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="Query vazia.")

    # Em vez de usar a query e filtros diretamente da requisição,
    # os derivamos da pergunta do usuário.
    # Nota: `request.filters` ainda pode ser usado se você quiser permitir filtros manuais da API.
    
    extraction_result = extract_filters_and_refine_query(q)
    query_text_for_embedding = extraction_result["query_refinada"]
    extracted_filters = extraction_result["filters"]

    query_vector = embed_text(query_text_for_embedding, is_query=True)
    logger.info(f"VETOR DA QUERY (primeiras 5 dims): {query_vector[:5]}")

    final_filters = request.filters or {}
    final_filters.update(extracted_filters)

    query_filter = None
    if final_filters:
        conds = []
        for field, value in final_filters.items():
            conds.append(qmodels.FieldCondition(key=field, match=qmodels.MatchValue(value=value)))
        if conds:
            query_filter = qmodels.Filter(must=conds)
            

    logger.info(query_vector)
    logger.info(query_filter)

    hits = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=max(3, request.top_k),
    )

    if not hits:
        return {"answer": "Desculpe, não encontrei conteúdo relevante.", "sources": []}

    context_snippets = []
    sources_info = []
    MAX_CHARS_PER_CHUNK = 2500

    for i, hit in enumerate(hits):
        payload = hit.payload or {}
        content = (payload.get("content", "") or "")[:MAX_CHARS_PER_CHUNK] 
        titulo = payload.get("titulo") or payload.get("title") or "Documento"
        autores = payload.get("autores") or "Desconhecido"
        orientador = payload.get("orientador") or "Desconhecido"
        ano = payload.get("ano") or payload.get("year") or ""
        evento = payload.get("evento") or ""
        area = payload.get("area") or ""
        link = payload.get("link") or payload.get("link_pdf") or ""
        snippet = content[:200] + ("..." if len(content) > 200 else "")
        context_snippets.append(f"- {titulo} ({ano}) — {evento} / {area}\n\"{content.strip()}\"")

        contexto_formatado = (
        f"[INÍCIO DO DOCUMENTO {i+1}]\n"
        f"Título: {titulo}\n"
        f"Autores: {autores}\n"
        f"Orientador: {payload.get('orientador', 'Não informado')}\n"
        f"Ano: {ano}\n"
        f"Evento: {evento}\n"
        f"Link: {link}\n"
        f"Conteúdo do trecho: \"{content.strip()}\"\n" 
        f"[FIM DO DOCUMENTO {i+1}]"
        )
        context_snippets.append(contexto_formatado)

        sources_info.append({
            "titulo": titulo,
            "autores": autores,
            "ano": ano,
            "evento": evento,
            "area": area,
            "link": link,
            "snippet": snippet
        })

    prompt = (
    "Você é um assistente de pesquisa preciso e factual. Sua tarefa é responder perguntas com base EXCLUSIVAMENTE nos trechos de documentos fornecidos a seguir.\n"
    "REGRAS IMPORTANTES:\n"
    "1. NÃO invente, infira ou adicione qualquer informação que não esteja explicitamente declarada nos documentos.\n"
    "2. Se a resposta para a pergunta não puder ser encontrada nos textos fornecidos, responda exatamente com: 'Com base nos documentos fornecidos, não encontrei informações para responder a essa pergunta.'\n"
    "3. Responda em HTML válido (<p>, <ul>, <li>, <a>, <strong>, <em>, <br>).\n\n"
    "--- DOCUMENTOS RELEVANTES ---\n"
    f"{chr(10).join(context_snippets)}\n\n"
    "--- FIM DOS DOCUMENTOS ---\n\n"
    f"Pergunta do usuário: {q}\n"
    "Resposta (HTML):"
    )

    try:
        import os
        model_name = getattr(settings, "llama_model_name", None) or os.path.basename(
            os.environ.get("LLAMA_MODEL_PATH", "local")
        )
        url = f"{settings.llama_api_base}/completions"
        payload = {
            "model": model_name,
            "prompt": prompt,
            "max_tokens": 512,
            "temperature": 0,
            "stop": ["Pergunta:"]
        }
        headers = {}
        if settings.llama_api_key:
            headers["Authorization"] = f"Bearer {settings.llama_api_key}"
        response = requests.post(url, json=payload, headers=headers, timeout=600)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"Falha ao chamar LLM: {e}")
        raise HTTPException(status_code=500, detail="Erro na geração da resposta.")

    # 7) extrai texto
    answer_text = ""
    if "choices" in data and data["choices"]:
        ch = data["choices"][0]
        answer_text = (ch.get("text") or ch.get("message", {}).get("content") or "").strip()
    else:
        answer_text = (data.get("content") or "").strip()

    logger.info(f"Q: {q}\nA len: {len(answer_text)}")
    return {"answer": answer_text, "sources": sources_info}
