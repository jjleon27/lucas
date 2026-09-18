"""
SPLIT (laboratorio) — sección AISLADA para probar, desde cero, la forma más
rápida y precisa de leer una boleta y sacar nombre/cantidad/valor de cada
ítem, sin tocar el flujo real de "Dividir cuenta" (`bills.py`/`ocr.py`) que
ya funciona y está validado.

Por qué existe (2026-09-18): el usuario reportó que ChatGPT "lee una boleta
en 2-4 segundos" y que acá tardamos 16-17s+, y pidió una sección nueva para
probar de cero antes de tocar lo que ya anda. Se midió con una llamada real
en streaming que el PRIMER token de texto visible llega en ~4.5s — lo que
tardaba 16-17s no era que el modelo fuera lento, era que la app esperaba la
respuesta COMPLETA (lectura + reformateo + posición) antes de mostrar
cualquier cosa. Esta sección prueba mostrar los ítems A MEDIDA que el modelo
los escribe (streaming), en vez de recién al final.

Ámbito deliberadamente chico: solo nombre/cantidad/valor por ítem, en
streaming. NADA de participantes/reparto/pago/liquidación — eso ya funciona
bien en `bills.py` y se reincorpora recién cuando esto esté validado (pedido
explícito del usuario).
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import get_current_user
from ..schemas import UserOut
from ..config import settings
from ..ocr import _prep_receipt_image  # única reutilización: prep de imagen ya validado (rotación EXIF, resize, contraste)

router = APIRouter(prefix="/split-lab", tags=["split-lab"])


# Prompt DISTINTO al de producción (`_RECEIPT_PROMPT_BILL` en ocr.py) a
# propósito — acá se pide una línea por ítem en un formato semi-fijo
# ("N | nombre | valor") pensado para poder detectar líneas completas
# MIENTRAS el modelo todavía está escribiendo, no al final. Ojo: la
# investigación previa de este mismo proyecto (docs/PLAN_split_v3.md cont.
# 13) encontró que forzarle un formato MÁQUINA (pipes) en el primer y ÚNICO
# paso empeoraba la precisión en boletas con líneas repetidas — acá se
# prueba de nuevo, deliberadamente, porque es justo la pregunta que hay que
# responder con datos antes de decidir si esta vía reemplaza al pipeline
# actual de 2 pasos.
_LAB_PROMPT = (
    "Lee esta boleta chilena. Por cada ítem de la boleta (uno por línea, en "
    "el mismo orden en que aparece), escribe exactamente:\n"
    "cantidad | nombre del ítem | valor total de esa línea (solo el número, sin $ ni puntos)\n"
    "No escribas nada más en esas líneas (sin viñetas, sin numeración extra). "
    "No incluyas la línea de total/subtotal/propina como si fuera un ítem. "
    "Al terminar todos los ítems, en una línea aparte escribe: "
    "TOTAL | <el monto total del consumo, sin propina>"
)

_ITEM_LINE_RE = re.compile(r"^\s*([\d.,]+)\s*\|\s*(.+?)\s*\|\s*([\d.,]+)\s*$")
_TOTAL_LINE_RE = re.compile(r"^\s*TOTAL\s*\|\s*([\d.,]+)\s*$", re.IGNORECASE)


def _parse_clp_number(tok: str) -> float:
    tok = tok.strip().replace("$", "")
    if not tok:
        return 0.0
    # "1.500" (chileno, punto de miles) vs "1500.50" (decimal real) — si el
    # único separador es un punto y quedan exactamente 3 dígitos después,
    # se asume separador de miles (igual criterio que `_parse_clp` en ocr.py).
    if "," in tok:
        tok = tok.replace(".", "").replace(",", ".")
    elif tok.count(".") == 1 and len(tok.split(".")[-1]) == 3:
        tok = tok.replace(".", "")
    try:
        return float(tok)
    except ValueError:
        return 0.0


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_RACE_N = 2  # copias en paralelo — ver docstring de _race_streams


def _race_streams(client, *, model: str, messages: list):
    """Dispara `_RACE_N` llamadas IDÉNTICAS en paralelo y va entregando los
    tokens de texto de la que responda primero — descarta las demás.

    Por qué: medido en vivo (2026-09-18, misma boleta, mismo código,
    subidas consecutivas) el tiempo al primer ítem varía 0.8s-14s — no es
    la subida (fotos ya comprimidas, chicas) ni nuestro código, es
    variabilidad real e inherente de la API (distribución de cola pesada,
    normal en LLMs — la mayoría de las respuestas son rápidas, pero una
    fracción sale mucho más lenta por variables fuera de nuestro control).
    Técnica real y medida en un blog de ingeniería (myhoai.com, "A simple
    fix for LLM tail latency"): mandar la misma solicitud 2 veces y usar la
    que responda primero baja el p99 de tiempo-al-primer-token de 4.2s a
    1.2s en sus datos — funciona porque una respuesta lenta es rara e
    independiente entre sí, así que la chance de que AMBAS copias salgan
    lentas a la vez es mucho menor que la chance de que una sola lo sea.

    Costo real, no escondido: duplica la cantidad de llamadas al modelo
    (se paga 2x en volumen) — aceptable acá porque SPLIT es justamente el
    laboratorio para probar esto antes de decidir si vale la pena en el
    flujo real, y porque el objetivo explícito de hoy es velocidad por
    sobre costo."""
    import queue
    import threading

    q: "queue.Queue" = queue.Queue()
    DONE = object()

    def worker(idx: int):
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, reasoning_effort="low", stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    q.put((idx, delta))
        except Exception as exc:  # noqa: BLE001
            q.put((idx, exc))
        finally:
            q.put((idx, DONE))

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(_RACE_N)]
    for t in threads:
        t.start()

    winner: Optional[int] = None
    finished: set[int] = set()
    while len(finished) < _RACE_N:
        idx, item = q.get()
        if item is DONE:
            finished.add(idx)
            if winner == idx:
                return  # el ganador terminó de escribir -> se acabó la carrera
            continue
        if isinstance(item, Exception):
            continue  # esta copia falló -- la otra puede seguir; si fallan las 2, el generador simplemente no entrega nada
        if winner is None:
            winner = idx
            print(f"[split-lab][race] copia {idx} ganó la carrera")
        if idx != winner:
            continue  # descartar tokens de la copia que perdió
        yield item


@router.post("/ocr-stream")
async def ocr_stream(
    file: UploadFile = File(...),
    current: UserOut = Depends(get_current_user),
):
    """SSE: emite cada ítem apenas el modelo termina de escribir su línea,
    no al final de toda la boleta. Eventos: `item` (uno por ítem),
    `done` (métricas finales: tiempo al primer ítem, tiempo total, ítems
    leídos). Nunca toca `Bill`/`BillItem` — es un sandbox de lectura, no
    persiste nada todavía."""
    image_bytes = await file.read()
    print(f"[split-lab][timing] imagen recibida: {len(image_bytes)/1024:.0f}KB")

    def gen():
        t0 = time.time()
        first_item_t: Optional[float] = None
        n_items = 0
        try:
            data_url, _, _, _ = _prep_receipt_image(image_bytes)
            print(f"[split-lab][timing] _prep_receipt_image={time.time()-t0:.2f}s")
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": f"no se pudo preparar la imagen: {exc}"})
            return

        if not settings.openai_api_key:
            yield _sse("error", {"message": "OPENAI_API_KEY no configurada"})
            return

        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key, timeout=60.0)

        buf = ""
        total_amount: Optional[float] = None
        got_any = False
        for delta in _race_streams(
            client,
            model=settings.openai_vision_model_bill,
            messages=[
                {"role": "system", "content": _LAB_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Lee la boleta."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        ):
            got_any = True
            buf += delta
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                m_total = _TOTAL_LINE_RE.match(line)
                if m_total:
                    total_amount = _parse_clp_number(m_total.group(1))
                    continue
                m = _ITEM_LINE_RE.match(line)
                if not m:
                    continue  # línea a medio escribir o ruido — se ignora, no se acusa
                qty_raw, name, value_raw = m.groups()
                qty = _parse_clp_number(qty_raw) or 1
                value = _parse_clp_number(value_raw)
                if first_item_t is None:
                    first_item_t = time.time() - t0
                n_items += 1
                yield _sse("item", {
                    "idx": n_items, "quantity": qty, "name": name.strip(), "line_total": value,
                    "t": round(time.time() - t0, 2),
                })

        if not got_any:
            yield _sse("error", {"message": "las 2 copias de la llamada al modelo fallaron"})
            return

        # última línea sin \n final (el stream puede cortar justo ahí)
        line = buf.strip()
        if line:
            m_total = _TOTAL_LINE_RE.match(line)
            if m_total:
                total_amount = _parse_clp_number(m_total.group(1))
            else:
                m = _ITEM_LINE_RE.match(line)
                if m:
                    qty_raw, name, value_raw = m.groups()
                    n_items += 1
                    yield _sse("item", {
                        "idx": n_items, "quantity": _parse_clp_number(qty_raw) or 1,
                        "name": name.strip(), "line_total": _parse_clp_number(value_raw),
                        "t": round(time.time() - t0, 2),
                    })

        total_t = time.time() - t0
        print(f"[split-lab][timing] primer_item={first_item_t if first_item_t else '-'} total={total_t:.2f}s n_items={n_items}")
        yield _sse("done", {
            "n_items": n_items, "total_amount": total_amount,
            "first_item_t": round(first_item_t, 2) if first_item_t else None,
            "total_t": round(total_t, 2),
        })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # evita que un proxy intermedio bufferee el stream
    })
