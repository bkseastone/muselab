"""Frost-erasure proof-of-human gate for the login boundary.

The token alone is a single shared secret - a bot can hammer `/api/files/*`
with guesses at line rate. This module adds a server-validated
proof-of-human-interaction step that must precede the token check at sign-in:
the browser presents a frost-covered canvas, the user wipes it with a real
pointer, and the client submits a *proof* (erased-coverage ratio, gesture
duration, move count) bound to a single-use, IP-scoped, nonce-stamped
challenge issued by `/api/login-challenge`.

Threat model & why each bound exists (mirrors the ECS-Terminal-Web reference):

  * nonce + single-use + IP binding  -> a captured/replayed proof is worthless;
    the attacker must solve a fresh challenge from their own address each try.
  * coverage ∈ [0.42, 1.2]            -> the user actually erased enough frost.
  * durationMs ∈ [300, ttl*1000]      -> not instantaneous (bot) nor stale.
  * moves ∈ [6, 3000]                 -> a real drag gesture, not one synthetic
    event; caps absurd values so a flood of fake moves can't pad coverage.
  * per-IP failure cap (8 / 10 min)   -> brute-force is throttled to a trickle
    before the token is ever compared. `_token_ok` stays constant-time, so even
    the throttled guesses leak nothing via timing.

The store is in-memory and per-process (single-worker muse deploy). State is
ephemeral by design: a challenge lives ≤5 min and is consumed on first use.
"""
import math
import secrets
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from .auth import _token_ok

router = APIRouter(prefix="/api", tags=["auth"])

# ---------------------------------------------------------------------------
# Tunables (deliberately module-level, mirroring the reference's top-level
# constants so the security-relevant bounds are auditable in one place).
# ---------------------------------------------------------------------------
LOGIN_CHALLENGE_TTL_SECONDS = 5 * 60      # a challenge is usable for 5 min
LOGIN_FAILURE_WINDOW_SECONDS = 10 * 60    # failure counter window
LOGIN_FAILURE_MAX = 8                     # -> 429 after 8 bad attempts / window
REQUIRED_COVERAGE = 0.42                  # ≥42% erased to count as human
MAX_COVERAGE = 1.2                        # tolerate rounding above 1.0
MIN_DURATION_MS = 300                     # a real drag is ≥300ms
MIN_MOVES = 6                             # a real drag is ≥6 pointer moves
MAX_MOVES = 3000                          # cap synthetic move flooding
MAX_OUTSTANDING_PER_KEY = 8               # memory hygiene: challenges per IP


def _finite(value) -> Optional[float]:
    """Coerce a JSON value to a finite float, else None.

    JSON bodies are untrusted; a bot may send `{"coverage": "abc"}`. We parse
    loosely (never raise) so the caller can route any malformed field to a
    plain verify-failure -> recorded failure -> rate-limit计数. Pydantic-level
    422s would otherwise skip the handler and let an attacker probe without
    touching the failure counter.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _client_key(request: Request) -> str:
    """Best-effort stable client identifier for challenge binding + rate limit.

    `X-Forwarded-For` first hop when muse sits behind nginx/Caddy/Cloudflare
    (without it every proxied request shares the proxy's address and the
    per-IP throttle becomes a global throttle). Falls back to the socket peer.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return "unknown"


class _LoginChallengeStore:
    """In-memory, thread-safe challenge + failure tracker.

    All mutations go through `_lock`. The critical sections are dict-only
    (microseconds), so holding the lock inside async handlers does not
    meaningfully block the event loop.
    """

    def __init__(self) -> None:
        self._challenges: dict[str, dict] = {}  # id -> challenge record
        self._failures: dict[str, dict] = {}    # client_key -> {count, reset_at}
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> float:
        return time.time()

    def _cleanup(self, now: float) -> None:
        for cid in [c for c, v in self._challenges.items()
                    if v["consumed"] or v["expires_at"] <= now]:
            del self._challenges[cid]
        for key in [k for k, v in self._failures.items() if v["reset_at"] <= now]:
            del self._failures[key]

    def create(self, client_key: str) -> dict:
        with self._lock:
            now = self._now()
            self._cleanup(now)
            # Bound outstanding challenges per IP so a single client cannot
            # grow memory indefinitely within the TTL window.
            own = [(cid, v) for cid, v in self._challenges.items()
                   if v["client_key"] == client_key]
            if len(own) >= MAX_OUTSTANDING_PER_KEY:
                oldest_id = min(own, key=lambda pair: pair[1]["created_at"])[0]
                del self._challenges[oldest_id]
            cid = secrets.token_urlsafe(24)
            nonce = secrets.token_urlsafe(16)
            self._challenges[cid] = {
                "client_key": client_key,
                "nonce": nonce,
                "created_at": now,
                "expires_at": now + LOGIN_CHALLENGE_TTL_SECONDS,
                "consumed": False,
            }
            return {
                "id": cid,
                "nonce": nonce,
                "minDurationMs": MIN_DURATION_MS,
                "requiredCoverage": REQUIRED_COVERAGE,
                "ttlSeconds": LOGIN_CHALLENGE_TTL_SECONDS,
            }

    def verify(self, client_key: str, proof: dict) -> bool:
        with self._lock:
            now = self._now()
            self._cleanup(now)
            ch = self._challenges.get(str(proof.get("id", "")))
            if not ch or ch["consumed"] or ch["expires_at"] <= now:
                return False
            if ch["client_key"] != client_key:
                return False
            if ch["nonce"] != str(proof.get("nonce", "")):
                return False
            coverage = _finite(proof.get("coverage"))
            duration_ms = _finite(proof.get("durationMs"))
            moves = _finite(proof.get("moves"))
            if coverage is None or coverage < REQUIRED_COVERAGE or coverage > MAX_COVERAGE:
                return False
            if (duration_ms is None or duration_ms < MIN_DURATION_MS
                    or duration_ms > LOGIN_CHALLENGE_TTL_SECONDS * 1000):
                return False
            if moves is None or moves < MIN_MOVES or moves > MAX_MOVES:
                return False
            ch["consumed"] = True
            return True

    def is_rate_limited(self, client_key: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else self._now()
        entry = self._failures.get(client_key)
        if not entry or entry["reset_at"] <= now:
            return False
        return entry["count"] >= LOGIN_FAILURE_MAX

    def record_failure(self, client_key: str, now: Optional[float] = None) -> None:
        now = now if now is not None else self._now()
        entry = self._failures.get(client_key)
        if not entry or entry["reset_at"] <= now:
            self._failures[client_key] = {
                "count": 1,
                "reset_at": now + LOGIN_FAILURE_WINDOW_SECONDS,
            }
            return
        entry["count"] += 1

    def clear_failures(self, client_key: str) -> None:
        self._failures.pop(client_key, None)


# Module-level singleton. conftest reloads every `backend.*` module per test,
# so each test gets a fresh store; in production there is one worker process.
store = _LoginChallengeStore()


class LoginPayload(BaseModel):
    # `challenge` is intentionally a loose dict, not a typed model: malformed
    # fields must flow through `verify` -> record_failure so the rate limiter
    # counts them. A strict model would 422 before the handler and let bots
    # probe the token boundary without ever tripping the throttle.
    challenge: dict = Field(default_factory=dict)
    token: str = ""


@router.post("/login-challenge")
async def create_login_challenge(request: Request) -> dict:
    """Issue a single-use, IP-bound challenge. Public (no token yet)."""
    return store.create(_client_key(request))


@router.post("/login")
async def login(payload: LoginPayload, request: Request) -> dict:
    """Verify the frost proof, then the token. Both are rate-limited per IP.

    Order matters: proof first, token second. A failed proof records a failure
    *before* the token is touched, so a bot cannot use the frost gate to
    amortize token guessing. A failed token (valid proof) also counts - the
    proof is consumed either way, forcing a fresh challenge next attempt.
    """
    key = _client_key(request)
    if store.is_rate_limited(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试。",
        )
    if not store.verify(key, payload.challenge):
        store.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先完成页面验证。",
        )
    if not _token_ok(payload.token):
        store.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 错误",
        )
    store.clear_failures(key)
    return {"ok": True}
