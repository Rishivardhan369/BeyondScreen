import contextvars
import re
import secrets

request_id = contextvars.ContextVar("request_id", default="-")

class RequestCorrelationMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        supplied = request.headers.get("X-Request-ID", "")
        identifier = supplied[:64] if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied) else secrets.token_hex(8)
        token = request_id.set(identifier)
        try:
            response = self.get_response(request); response["X-Request-ID"] = identifier; return response
        finally: request_id.reset(token)

class RequestIdFilter:
    def filter(self, record): record.request_id = request_id.get(); return True
