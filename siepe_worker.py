# siepe_worker.py
import os
import tempfile
import logging
from typing import Iterable, List, Tuple, Optional, Dict, Callable

import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
from ingest import ingest_pdf  # seu pipeline existente

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[Dict], None]]  # NOVO: callback de progresso

def limpar_texto(texto: str) -> str:
    return " ".join(texto.split())

def baixar_pdf_para_tmp(url: str) -> str:
    with requests.get(url, stream=True, timeout=(10, 60)) as r:
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir="/tmp") as tmp:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    tmp.write(chunk)
            return tmp.name

def verificar_pdf_ok(caminho_pdf: str) -> bool:
    try:
        with fitz.open(caminho_pdf) as doc:
            return doc.page_count > 0
    except Exception:
        return False

def criar_pagina_info_fitz(apresentador: str, titulo: str, autores: str,
                           orientador: str, evento: str, area: str,
                           ano: str, link_pdf: str) -> fitz.Document:
    doc = fitz.open()
    rect = fitz.paper_rect("a4")
    page = doc.new_page(width=rect.width, height=rect.height)
    texto = (
        f"Apresentador(a): {apresentador}\n"
        f"Título: {titulo}\n"
        f"Autores: {autores}\n"
        f"Orientador(a): {orientador}\n"
        f"Evento: {evento}\n"
        f"Área: {area}\n"
        f"Ano: {ano}\n"
        f"Link para PDF: {link_pdf}\n"
    )
    box = fitz.Rect(72, 72, rect.width - 72, rect.height - 72)
    page.insert_textbox(box, texto, fontsize=12, fontname="helv", align=0)
    return doc

def mesclar_info_e_pdf(info_doc: fitz.Document, caminho_pdf: str) -> str:
    merged = fitz.open()
    try:
        merged.insert_pdf(info_doc)
        with fitz.open(caminho_pdf) as orig:
            merged.insert_pdf(orig)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir="/tmp") as tmp_out:
            merged.save(tmp_out.name, deflate=True, garbage=4)
            return tmp_out.name
    finally:
        merged.close()

def parse_tabela_trabalhos(html: bytes) -> List[Tuple[str, str, str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")[1:]
    itens = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) != 5:
            continue
        apresentador = limpar_texto(cols[0].get_text(strip=True))
        titulo = limpar_texto(cols[1].get_text(strip=True))
        autores = limpar_texto(cols[2].get_text(strip=True))
        orientador = limpar_texto(cols[3].get_text(strip=True))
        a = cols[4].find("a")
        if not a or not a.get("href"):
            continue
        link_pdf = a["href"]
        itens.append((apresentador, titulo, autores, orientador, link_pdf))
    return itens

def processar_url(ano: str, area_code: str, area_nome: str,
                  evento_code: str, evento_nome: str,
                  max_itens: Optional[int] = None,
                  on_item: ProgressCb = None) -> Dict:
    """
    Para cada item: baixa -> cria página info -> mescla -> ingere -> apaga.
    Emite eventos no callback `on_item`.
    """
    url = f"https://cti.ufpel.edu.br/siepe/anais/{ano}/{area_code}/{evento_code}"
    try:
        r = requests.get(url, timeout=(30, 90))
        logger.info("REQUEST")
        logger.info(r.content)
        r.raise_for_status()
        itens = parse_tabela_trabalhos(r.content)
    except Exception as e:
        logger.error(f"[PAGE ERROR] Falha ao carregar/parsear página: {url} -> {e}")
        if on_item:
            on_item({
                "event": "page_done",
                "ano": ano, "area_code": area_code, "area_nome": area_nome,
                "evento_code": evento_code, "evento_nome": evento_nome,
                "url": url, "ok": 0, "falha": 0, "total": 0, "error": str(e)
            })

        return {"url": url, "total_listados": 0, "ok": 0, "falha": 0, "erros": [str(e)]}

    total = len(itens)
    if max_itens is not None:
        itens = itens[:max_itens]

    if on_item:
        on_item({
            "event": "page_start", "ano": ano, "area_code": area_code, "area_nome": area_nome,
            "evento_code": evento_code, "evento_nome": evento_nome, "url": url, "total": total
        })

    ok = 0
    falha = 0
    erros: List[str] = []

    for idx, (apresentador, titulo, autores, orientador, link_pdf) in enumerate(itens, start=1):
        meta_base = {
            "ano": ano, "area_code": area_code, "area_nome": area_nome,
            "evento_code": evento_code, "evento_nome": evento_nome,
            "titulo": titulo, "link_pdf": link_pdf, "idx": idx, "total": total
        }
        if on_item:
            on_item({"event": "item_start", **meta_base})

        orig_tmp = None
        merged_tmp = None
        try:
            orig_tmp = baixar_pdf_para_tmp(link_pdf)
            if not verificar_pdf_ok(orig_tmp):
                raise RuntimeError("PDF vazio/corrompido")

            info_doc = criar_pagina_info_fitz(
                apresentador, titulo, autores, orientador,
                evento_nome, area_nome, str(ano), link_pdf
            )
            try:
                merged_tmp = mesclar_info_e_pdf(info_doc, orig_tmp)
            finally:
                info_doc.close()

            ingest_pdf(merged_tmp)
            ok += 1
            if on_item:
                on_item({"event": "item_done", "status": "ok", **meta_base})
        except Exception as e:
            falha += 1
            msg = f"{titulo}: {e}"
            erros.append(msg)
            if on_item:
                on_item({"event": "item_done", "status": "error", "error": str(e), **meta_base})
        finally:
            for p in (orig_tmp, merged_tmp):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    if on_item:
        on_item({
            "event": "page_done", "ano": ano, "area_code": area_code, "area_nome": area_nome,
            "evento_code": evento_code, "evento_nome": evento_nome,
            "url": url, "ok": ok, "falha": falha, "total": total
        })

    return {"url": url, "total_listados": total, "ok": ok, "falha": falha, "erros": erros}

def processar_todos(anos: Iterable[str] = None,
                    areas: Iterable[Tuple[str, str]] = None,
                    eventos: Iterable[Tuple[str, str]] = None,
                    max_itens_por_pagina: Optional[int] = None,
                    on_item: ProgressCb = None) -> Dict:
    if anos is None:
        anos = ["2015","2016","2017","2018","2019","2020","2021","2022","2023","2024"]
    if areas is None:
        areas = [
            ("ca","Ciências Agrárias"), ("cb","Ciências Biológicas"),
            ("ce","Ciências Exatas e da Terra"), ("ch","Ciências Humanas"),
            ("cs","Ciências da Saúde"), ("sa","Ciências Sociais Aplicadas"),
            ("en","Engenharias"), ("la","Linguística, Letras e Artes"),
            ("md","Multidisciplinar"), ("G1","Diversidade no Ensino Superior"),
            ("G2","Tecnologias Educacionais na Educação Superior"),
            ("G3","Projetos e Programas Institucionais"),
            ("G4","Monitorias"), ("G5","Relato de experiência na Graduação"),
        ]
    if eventos is None:
        eventos = [
            ("ceg","Congresso de Ensino de Graduação"),
            ("cic","Congresso de Iniciação Científica"),
            ("cit","Congresso de Inovação Tecnológica"),
            ("enpos","Encontro de Pós Graduação"),
        ]

    resumo = {"total_paginas": 0, "ok": 0, "falha": 0, "detalhes": []}
    for ano in anos:
        for area_code, area_nome in areas:
            for evento_code, evento_nome in eventos:
                url = f"https://cti.ufpel.edu.br/siepe/anais/{ano}/{area_code}/{evento_code}"
                try:
                    r = processar_url(
                        ano=ano, area_code=area_code, area_nome=area_nome,
                        evento_code=evento_code, evento_nome=evento_nome,
                        max_itens=max_itens_por_pagina, on_item=on_item
                    )
                except Exception as e:
                    # Última linha de defesa: loga, sinaliza e continua
                    logger.error(f"[FATAL PAGE ERROR] {url}: {e}")
                    if on_item:
                        on_item({
                            "event": "page_done", "ano": ano, "area_code": area_code, "area_nome": area_nome,
                            "evento_code": evento_code, "evento_nome": evento_nome,
                            "url": url, "ok": 0, "falha": 0, "total": 0, "error": str(e)
                        })
                    r = {"url": url, "total_listados": 0, "ok": 0, "falha": 0, "erros": [str(e)]}

                resumo["total_paginas"] += 1
                resumo["ok"] += r.get("ok", 0)
                resumo["falha"] += r.get("falha", 0)
                resumo["detalhes"].append(r)
    return resumo
