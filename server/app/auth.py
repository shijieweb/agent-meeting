# -*- coding: utf-8 -*-
"""R1 令牌闸：身份鉴权（叠加于 agent_exists 白名单之外）。仅 stdlib，零新增依赖。"""
import base64, hashlib, hmac, ipaddress, json, logging, os, time
from fastapi import HTTPException, Request

logger = logging.getLogger("am_auth")
TRUSTED_PROXIES = {"127.0.0.1", "::1"}
_ESC_BASE = [ipaddress.ip_network("127.0.0.1"), ipaddress.ip_network("::1")]

def _escape_nets():
    nets = list(_ESC_BASE)
    raw = os.environ.get("AM_ESCAPE_IPS", "")
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("AM_ESCAPE_IPS 含非法项，已忽略: %r", item)
    return nets

def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    xff = request.headers.get("X-Forwarded-For")
    if xff and peer in TRUSTED_PROXIES:
        return xff.split(",")[0].strip()
    return peer or "unknown"

def _is_escape(request: Request) -> bool:
    ip = _client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _escape_nets())

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

def _b64d(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _parse_ttl(ttl: str) -> int:
    ttl = (ttl or "30d").strip()
    try:
        if ttl.endswith("d"):
            return int(ttl[:-1]) * 86400
        if ttl.endswith("h"):
            return int(ttl[:-1]) * 3600
        return int(ttl)
    except ValueError:
        return 30 * 86400

def sign_token(ttl: str = None) -> str:
    secret = os.environ["AM_TOKEN_SECRET"].encode()
    ttl_sec = _parse_ttl(ttl or os.environ.get("AM_TOKEN_TTL", "30d"))
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64u(json.dumps({"iat": int(time.time()), "exp": int(time.time()) + ttl_sec}).encode())
    sig = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64u(sig)}"

def verify_token(token: str):
    mode = os.environ.get("AM_TOKEN_MODE", "jwt")
    if mode == "static":
        exp = os.environ.get("AM_STATIC_TOKEN", "")
        return (bool(exp) and token == exp), ("ok" if (bool(exp) and token == exp) else "invalid")
    secret = os.environ.get("AM_TOKEN_SECRET")
    if not secret:
        return False, "invalid"
    try:
        h, p, s = token.split(".")
        sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(s, _b64u(sig)):
            return False, "invalid"
        payload = json.loads(_b64d(p))
        if payload.get("exp", 0) < int(time.time()):
            return False, "expired"
        return True, "ok"
    except Exception:
        return False, "invalid"

def require_write_auth(request: Request) -> None:
    if _is_escape(request):
        logger.info("escape_bypass ip=%s path=%s", _client_ip(request), request.url.path)
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("token_missing ip=%s path=%s", _client_ip(request), request.url.path)
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    ok, reason = verify_token(auth[len("Bearer "):].strip())
    if not ok:
        logger.warning("token_%s ip=%s path=%s", reason, _client_ip(request), request.url.path)
        raise HTTPException(status_code=401, detail="invalid or expired token")
