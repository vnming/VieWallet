"""Provider webhook inbox: ``POST /api/v1/webhooks/providers/{provider_code}``.

Exempt from the CSRF header (the provider is not a browser); authenticity comes from the HMAC
signature over the raw body. A stored callback answers 2xx quickly; processing is async.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.providers.service import ProviderServices, receive_callback

router = APIRouter(prefix="/api/v1/webhooks/providers", tags=["webhooks"])


def services(request: Request) -> ProviderServices:
    value: ProviderServices = request.app.state.providers
    return value


@router.post("/{provider_code}", include_in_schema=False)
async def provider_webhook(provider_code: str, request: Request) -> JSONResponse:
    ctx = services(request)
    provider = ctx.providers.get(provider_code.upper())
    if provider is None:
        return JSONResponse({"code": "NOT_FOUND"}, status_code=404)
    raw = await request.body()
    async with ctx.db.transaction() as session:
        result = await receive_callback(session, ctx.clock, provider, request.headers, raw)
    if result.status == "REJECTED":
        return JSONResponse({"code": "INVALID_SIGNATURE"}, status_code=401)
    return JSONResponse({"status": result.status.lower()}, status_code=200)
