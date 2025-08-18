# pull_models.py
import os
from huggingface_hub import snapshot_download, hf_hub_download

MODELS_ROOT = "/models"

# Variáveis vindas do .env
EMBEDDING_MODEL_ID = os.environ.get("EMBEDDING_MODEL_ID", "intfloat/multilingual-e5-base")
EMBEDDING_LOCAL_PATH = os.environ.get("EMBEDDING_LOCAL_PATH", "/models/embeddings/intfloat__multilingual-e5-base")
LLM_HF_REPO_ID = os.environ.get("LLM_HF_REPO_ID", "Qwen/Qwen2.5-3B-Instruct-GGUF")
LLM_HF_FILENAME = os.environ.get("LLM_HF_FILENAME", "qwen2.5-3b-instruct-q4_k_m.gguf")
HF_TOKEN = os.environ.get("HF_TOKEN", None)

os.makedirs(MODELS_ROOT, exist_ok=True)
os.makedirs(os.path.dirname(EMBEDDING_LOCAL_PATH), exist_ok=True)
os.makedirs("/models/llm", exist_ok=True)

def sanitize(model_id: str) -> str:
    return model_id.replace("/", "__")

def ensure_embeddings():
    if os.path.exists(EMBEDDING_LOCAL_PATH) and os.listdir(EMBEDDING_LOCAL_PATH):
        print(f"[puller] Embeddings já presentes em {EMBEDDING_LOCAL_PATH}")
        return
    print(f"[puller] Baixando embeddings: {EMBEDDING_MODEL_ID} -> {EMBEDDING_LOCAL_PATH}")
    snapshot_download(
        repo_id=EMBEDDING_MODEL_ID,
        local_dir=EMBEDDING_LOCAL_PATH,
        local_dir_use_symlinks=False,
        token=HF_TOKEN
    )
    print("[puller] Embeddings prontos.")

def ensure_llm():
    target_path = f"/models/llm/{LLM_HF_FILENAME}"
    if os.path.exists(target_path):
        print(f"[puller] LLM GGUF já presente em {target_path}")
        return
    print(f"[puller] Baixando LLM GGUF: {LLM_HF_REPO_ID}/{LLM_HF_FILENAME}")
    p = hf_hub_download(
        repo_id=LLM_HF_REPO_ID,
        filename=LLM_HF_FILENAME,
        local_dir="/models/llm",
        local_dir_use_symlinks=False,
        token=HF_TOKEN
    )
    # garante nome exato
    if p != target_path and os.path.exists(p):
        os.rename(p, target_path)
    print("[puller] LLM GGUF pronto.")

if __name__ == "__main__":
    ensure_embeddings()
    ensure_llm()
    # marca prontidão
    open("/models/.ready", "w").close()
    print("[puller] Done. (/models/.ready criado)")
