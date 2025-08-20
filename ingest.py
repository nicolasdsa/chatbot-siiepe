# ingest.py
import os, re, uuid, logging
import fitz
from transformers import AutoTokenizer, AutoModel
import torch
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from settings import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

emb_path = settings.embedding_local_path or settings.embedding_model
logger.info(f"Carregando embeddings de: {emb_path}")
tokenizer = AutoTokenizer.from_pretrained(emb_path)
model = AutoModel.from_pretrained(emb_path)
model.eval()
device = torch.device("cpu")
model.to(device)

def embed_text(text: str, is_query: bool = False) -> list[float]:
    if "multilingual-e5" in (settings.embedding_model.lower() if settings.embedding_model else "") \
       or "multilingual-e5" in emb_path.lower():
        text = ("query: " if is_query else "passage: ") + text
    inputs = tokenizer(text, return_tensors='pt', truncation=True, max_length=512)
    with torch.no_grad():
        outputs = model(**inputs.to(device))
    last_hidden = outputs.last_hidden_state
    attention = inputs['attention_mask'].unsqueeze(-1).expand(last_hidden.size()).to(last_hidden)
    masked_hidden = last_hidden * attention
    sum_hidden = masked_hidden.sum(dim=1)
    count_tokens = attention.sum(dim=1)
    embedding = sum_hidden / count_tokens
    embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)
    return embedding.cpu().tolist()[0]

qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
COLLECTION_NAME = "articles"
VECTOR_SIZE = model.config.hidden_size
distance = qmodels.Distance.COSINE
try:
    qdrant.get_collection(COLLECTION_NAME)
except Exception:
    logger.info(f"Criando coleção {COLLECTION_NAME} (dim={VECTOR_SIZE})")
    qdrant.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qmodels.VectorParams(size=VECTOR_SIZE, distance=distance)
    )

META_FIELDS = {
    "apresentador": re.compile(r"Apresentador\(a\):\s*(.+)", re.IGNORECASE),
    "titulo": re.compile(r"T[íi]tulo:\s*(.+)", re.IGNORECASE),
    "autores": re.compile(r"Autores?:\s*(.+)", re.IGNORECASE),
    "orientador": re.compile(r"Orientador\(a\):\s*(.+)", re.IGNORECASE),
    "evento": re.compile(r"Evento:\s*(.+)", re.IGNORECASE),
    "area": re.compile(r"\b[ÁA]rea:\s*(.+)", re.IGNORECASE),
    "ano": re.compile(r"Ano:\s*(\d{4})", re.IGNORECASE),
    "link": re.compile(r"Link\s*para\s*PDF:\s*(http[s]?://\S+)", re.IGNORECASE)
}

def extract_metadata(first_page_text: str) -> dict:
    meta = {}
    for key, pattern in META_FIELDS.items():
        m = pattern.search(first_page_text)
        if m:
            meta[key] = m.group(1).strip()
    return meta

def chunk_text(text: str, max_tokens: int = 800, overlap_tokens: int = 50) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current, cur_len = [], [], 0
    for para in paragraphs:
        toks = para.split()
        L = len(toks)
        if L > max_tokens:
            step = max_tokens - overlap_tokens
            for i in range(0, L, step):
                sub = toks[i:i+max_tokens]
                chunks.append(" ".join(sub))
            current, cur_len = [], 0
            continue
        if cur_len + L <= max_tokens:
            current.append(para)
            cur_len += L
        else:
            if current:
                chunks.append(" ".join(current))
            overlap = []
            if chunks:
                last = chunks[-1].split()
                overlap = last[-overlap_tokens:] if len(last) >= overlap_tokens else last
            current = [" ".join(overlap), para] if overlap else [para]
            cur_len = len(overlap) + L
    if current:
        chunks.append(" ".join(current))
    return chunks

def ingest_pdf(file_path: str):
    logger.info(f"Ingestando: {file_path}")
    doc = fitz.open(file_path)
    meta = {}
    if doc.page_count > 0:
        first_text = doc.load_page(0).get_text("text")
        meta = extract_metadata(first_text)
    full_text = ""
    for p in doc:
        full_text += p.get_text("text") + "\n"
    doc.close()
    if "titulo" in meta:
        meta["titulo"] = meta["titulo"].strip()
    doc_id = str(uuid.uuid4())
    meta["doc_id"] = doc_id

    chunks = chunk_text(full_text, max_tokens=800, overlap_tokens=50)
    logger.info(f"{len(chunks)} chunks")
    points = []
    for idx, ch in enumerate(chunks):
        vec = embed_text(ch, is_query=False)
        logger.info(f"VETOR DO CHUNK {idx} (primeiras 5 dims): {vec[:5]}")
        payload = meta.copy()
        payload["chunk_index"] = idx
        payload["content"] = ch
        pid = str(uuid.uuid4()) 
        points.append(qmodels.PointStruct(id=pid, vector=vec, payload=payload))
    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info("OK")

if __name__ == "__main__":
    import sys, os
    if len(sys.argv) < 2:
        print("Uso: python ingest.py <arquivo_ou_pasta>")
        raise SystemExit(1)
    tgt = sys.argv[1]
    if os.path.isdir(tgt):
        for n in os.listdir(tgt):
            if n.lower().endswith(".pdf"):
                ingest_pdf(os.path.join(tgt, n))
    elif tgt.lower().endswith(".pdf"):
        ingest_pdf(tgt)
    else:
        print("Forneça um PDF ou pasta com PDFs.")
