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

        try:
            stream = client.chat.completions.create(
                model=settings.openai_vision_model_bill,
                messages=[
                    {"role": "system", "content": _LAB_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Lee la boleta."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ],
                reasoning_effort="low",
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": f"error al llamar al modelo: {exc}"})
            return

        buf = ""
        total_amount: Optional[float] = None
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
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
