"""
Shared rate-limiter instance. Import `limiter` everywhere and apply
@limiter.limit("N/minute") to sensitive endpoints.

Key: real client IP, preferring X-Forwarded-For (Railway/Vercel proxies).
Storage: in-memory (restarts reset counts — acceptable for MVP).
"""
from fastapi import Request
from slowapi import Limiter


def _real_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_real_ip)
