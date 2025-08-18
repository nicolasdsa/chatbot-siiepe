# app.py
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from settings import settings
from ingest import ingest_pdf, qdrant, COLLECTION_NAME, qmodels, embed_text
from siepe_worker import processar_todos, processar_url

# --------------------------------------------
# Configuração básica / logging
# --------------------------------------------
logger = logging.getLogger("rag_api")
logger.setLevel(logging.INFO)

app = FastAPI(title="RAG API", version="1.0")

# CORS (se habilitado nas settings)
if settings.enable_cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --------------------------------------------
# Auth simples via Bearer <RAG_TOKEN>
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
    areas: Optional[List[str]] = None      # ex: ["en","ce"]
    eventos: Optional[List[str]] = None    # ex: ["cic","cit"]
    max_itens_por_pagina: Optional[int] = None
    somente_esta_pagina: Optional[dict] = None  # {"ano":"2024","area":"en","evento":"cic"}

class ChatMessage(BaseModel):
    role: str
    content: str

class QueryRequest(BaseModel):
    q: str
    top_k: int = 3
    filters: dict | None = None
    hybrid: bool = False  # reservado p/ futura BM25 + vetorial (não usado aqui)

# --------------------------------------------
# Ingest job infra (bem simples)
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
# Query simples (sem memória / sem metas rígidos)
# --------------------------------------------
@app.post("/query", dependencies=[Depends(verify_token)])
def query_endpoint(request: QueryRequest):
    q = (request.q or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="Query vazia.")

    # 1) embedding da query
    query_vector = embed_text(q, is_query=True)
    logger.info(f"VETOR DA QUERY (primeiras 5 dims): {query_vector[:5]}")

    # 2) filtro simples (se veio)
    query_filter = None
    if request.filters:
        conds = []
        for field, value in request.filters.items():
            conds.append(qmodels.FieldCondition(key=field, match=qmodels.MatchValue(value=value)))
        if conds:
            query_filter = qmodels.Filter(must=conds)

    # 3) busca no Qdrant
    hits = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=max(1, request.top_k),
    )

    if not hits:
        return {"answer": "Desculpe, não encontrei conteúdo relevante.", "sources": []}

    # 4) monta contexto e fontes
    context_snippets = []
    sources_info = []
    for hit in hits:
        payload = hit.payload or {}
        content = payload.get("content", "")
        titulo = payload.get("titulo") or payload.get("title") or "Documento"
        autores = payload.get("autores") or "Desconhecido"
        ano = payload.get("ano") or payload.get("year") or ""
        evento = payload.get("evento") or ""
        area = payload.get("area") or ""
        link = payload.get("link") or payload.get("link_pdf") or ""
        snippet = content[:200] + ("..." if len(content) > 200 else "")
        context_snippets.append(f"- {titulo} ({ano}) — {evento} / {area}\n\"{content.strip()}\"")
        sources_info.append({
            "titulo": titulo,
            "autores": autores,
            "ano": ano,
            "evento": evento,
            "area": area,
            "link": link,
            "snippet": snippet
        })

    # 5) prompt para o LLM (HTML na resposta)
    prompt = (
        "Você é um assistente que responde com base nos documentos fornecidos.\n"
        "Use APENAS os trechos abaixo para responder. Responda em HTML válido "
        "(<p>, <ul>, <li>, <a>, <strong>, <em>, <br>), sem inventar informações.\n\n"
        "Documentos relevantes:\n"
        f"{chr(10).join(context_snippets)}\n\n"
        f"Pergunta: {q}\nResposta (HTML):"
    )

    # 6) chamada ao LLM (llama.cpp compatível). Incluímos 'model' se estiver configurado.
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
            "temperature": 0.2,
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
