"""M&Q AI CONNECT — pre-provider refusal screen (EN + ES).

Runs BEFORE any provider call. A hit costs nothing and risks nothing:
the canned refusal is returned and the model is never invoked. Pattern
adapted from the M&Q Copilot's proven EN/ES execution screen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = [
    ("execution", re.compile(
        r"\b(place|submit|execute|enter|buy|sell|short|long)\b.{0,40}"
        r"\b(trade|order|mnq|nq|es|contract|contracts|position)\b"
        r"|\b(compra|vende|ejecuta|coloca|abre)\b.{0,40}"
        r"\b(operaci[oó]n|orden|contrato|posici[oó]n|mnq)\b"
        r"|\bshould i (buy|sell|enter|trade)\b"
        r"|\bdebo (comprar|vender|entrar|operar)\b", re.I)),
    ("prediction", re.compile(
        r"\b(will|going to|gonna)\b.{0,30}\b(go up|go down|rise|fall|pump|dump|moon)\b"
        r"|\bprice prediction\b|\bpredicci[oó]n\b|\bva a (subir|bajar)\b"
        r"|\bwhat('s| is) the price (of|right now|now)\b|\bprecio (actual|de ahora)\b"
        r"|\bgive me a signal\b|\bdame una se[ñn]al\b", re.I)),
    ("profit", re.compile(
        r"\bhow much (can|will|do) i (make|earn|profit)\b"
        r"|\bguarantee(d)?\b.{0,20}\b(profit|return|money|ganancia)"
        r"|\bcu[aá]nto (puedo|voy a) ganar\b|\bganancias garantizadas\b", re.I)),
    ("personal_advice", re.compile(
        r"\bshould i risk\b|\bmy account\b.{0,30}\b(risk|trade|buy|sell)\b"
        r"|\bdebo arriesgar\b|\bmi cuenta\b.{0,30}\b(arriesgar|operar|comprar|vender)\b",
        re.I)),
]

REFUSALS = {
    "execution": {
        "en": "I can't place, execute, or direct trades — I teach and explain records only. If you'd like, ask me about the lesson or record behind the idea.",
        "es": "No puedo colocar, ejecutar ni dirigir operaciones — solo enseño y explico registros. Si quieres, pregúntame por la lección o el registro detrás de la idea.",
    },
    "prediction": {
        "en": "I don't predict prices and I have no live market feed. I can explain the method the curriculum teaches for reading the market yourself.",
        "es": "No predigo precios y no tengo datos de mercado en vivo. Puedo explicarte el método que el plan de estudios enseña para leer el mercado por ti mismo.",
    },
    "profit": {
        "en": "No one can promise trading profits, and I won't either. What I can do is explain the risk rules and the method, exactly as they're taught.",
        "es": "Nadie puede prometer ganancias operando, y yo tampoco lo haré. Lo que sí puedo hacer es explicar las reglas de riesgo y el método, tal como se enseñan.",
    },
    "personal_advice": {
        "en": "I can't advise on your personal account or money. I can teach the curriculum's risk rules so you can decide for yourself.",
        "es": "No puedo aconsejarte sobre tu cuenta o tu dinero personal. Puedo enseñarte las reglas de riesgo del plan de estudios para que decidas por ti mismo.",
    },
}


@dataclass
class ScreenHit:
    kind: str
    refusal: str


def screen(text: str, lang: str = "en") -> ScreenHit | None:
    lang = "es" if lang == "es" else "en"
    for kind, pat in _PATTERNS:
        if pat.search(text or ""):
            return ScreenHit(kind=kind, refusal=REFUSALS[kind][lang])
    return None
