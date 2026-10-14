"""Small OpenAI-compatible planner client with explicit budget and secret boundaries."""
from __future__ import annotations

import json
import os
import socket
import time
import ctypes
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse


OFFICIAL_PRICES_CNY_PER_MILLION = {
    # Peak prices are deliberately used as the safety bound.
    "deepseek-flash": {"input": Decimal("2"), "cache": Decimal("0.04"), "output": Decimal("8")},
    "deepseek-v4-pro": {"input": Decimal("9"), "cache": Decimal("0.30"), "output": Decimal("27")},
}
OFFICIAL_PRICING_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


class ProviderError(RuntimeError):
    """A safe provider error that never includes credentials or response bodies."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def load_windows_dpapi_key(path_value: str) -> str:
    """Decrypt one small current-user DPAPI blob without persisting plaintext."""
    if os.name != "nt":
        raise ValueError("B2_DPAPI_KEY_FILE requires Windows")
    path = Path(path_value)
    if not path.is_absolute() or not path.is_file() or not 1 <= path.stat().st_size <= 8192:
        raise ValueError("B2_DPAPI_KEY_FILE must be an existing absolute path to a small credential blob")
    encrypted = path.read_bytes()
    crypto = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    incoming_buffer = ctypes.create_string_buffer(encrypted)
    incoming = _Blob(len(encrypted), ctypes.cast(incoming_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _Blob()
    crypto.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypto.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not crypto.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
            raise ValueError("DPAPI credential could not be decrypted for the current Windows user")
        key_bytes = ctypes.string_at(outgoing.data, outgoing.size)
        key = key_bytes.decode("utf-8")
        if not key.startswith("sk-") or not 16 <= len(key) <= 512 or any(char.isspace() for char in key):
            raise ValueError("Decrypted credential has an invalid API key shape")
        return key
    finally:
        if outgoing.data:
            kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))
        ctypes.memset(incoming_buffer, 0, ctypes.sizeof(incoming_buffer))


def _positive_decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as error:
        raise ValueError(f"{name} must be a decimal") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    api_key_source: str
    timeout_seconds: int = 45
    max_output_tokens: int = 700
    input_price_cny_per_million: Decimal | None = None
    cache_price_cny_per_million: Decimal | None = None
    output_price_cny_per_million: Decimal | None = None
    pricing_source: str | None = None
    official_deepseek: bool = False

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def pricing_known(self) -> bool:
        return self.input_price_cny_per_million is not None and self.output_price_cny_per_million is not None

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        if os.environ.get("B2_API_KEY"):
            key_source, api_key = "B2_API_KEY", os.environ["B2_API_KEY"]
        elif os.environ.get("DEEPSEEK_API_KEY"):
            key_source, api_key = "DEEPSEEK_API_KEY", os.environ["DEEPSEEK_API_KEY"]
        elif os.environ.get("B2_DPAPI_KEY_FILE"):
            key_source = "B2_DPAPI_KEY_FILE (Windows current-user DPAPI)"
            api_key = load_windows_dpapi_key(os.environ["B2_DPAPI_KEY_FILE"])
        else:
            key_source, api_key = "DEEPSEEK_API_KEY", ""
        if not api_key:
            raise ValueError("Missing B2_API_KEY, DEEPSEEK_API_KEY, or B2_DPAPI_KEY_FILE; no API call was made")
        base_url = os.environ.get("B2_API_BASE_URL", "https://api.deepseek.com").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("B2_API_BASE_URL must be a plain HTTPS origin/path without credentials, query, or fragment")
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Local provider endpoints are not accepted by this B2 client")
        model = os.environ.get("B2_MODEL", "deepseek-flash").strip()
        if not model or len(model) > 200:
            raise ValueError("B2_MODEL is missing or too long")
        try:
            timeout = int(os.environ.get("B2_TIMEOUT_SECONDS", "45"))
            max_tokens = int(os.environ.get("B2_MAX_OUTPUT_TOKENS", "700"))
        except ValueError as error:
            raise ValueError("B2 timeout and token limits must be integers") from error
        if not 5 <= timeout <= 120 or not 64 <= max_tokens <= 1200:
            raise ValueError("B2 timeout must be 5-120 seconds and output limit 64-1200 tokens")

        official = parsed.hostname == "api.deepseek.com"
        if official and model in OFFICIAL_PRICES_CNY_PER_MILLION:
            price = OFFICIAL_PRICES_CNY_PER_MILLION[model]
            input_price, cache_price, output_price = price["input"], price["cache"], price["output"]
            pricing_source = OFFICIAL_PRICING_URL + " (peak-price safety bound, checked 2026-10-13)"
        else:
            names = ("B2_INPUT_PRICE_CNY_PER_M", "B2_CACHE_PRICE_CNY_PER_M", "B2_OUTPUT_PRICE_CNY_PER_M")
            values = [os.environ.get(name) for name in names]
            if not all(values):
                raise ValueError("Custom gateway/model requires all three B2_*_PRICE_CNY_PER_M values before paid calls")
            input_price, cache_price, output_price = (
                _positive_decimal(value, name) for value, name in zip(values, names)
            )
            pricing_source = "user-supplied gateway price configuration; invoice not independently verified"
        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_source=key_source,
            timeout_seconds=timeout,
            max_output_tokens=max_tokens,
            input_price_cny_per_million=input_price,
            cache_price_cny_per_million=cache_price,
            output_price_cny_per_million=output_price,
            pricing_source=pricing_source,
            official_deepseek=official,
        )

    def public_dict(self) -> dict:
        return {
            "base_url_host": urlparse(self.base_url).hostname,
            "base_url_path": urlparse(self.base_url).path or "/",
            "model_requested": self.model,
            "api_key_source": self.api_key_source,
            "api_key_value_saved": False,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "official_deepseek": self.official_deepseek,
            "pricing_source": self.pricing_source,
            "input_price_cny_per_million": str(self.input_price_cny_per_million),
            "cache_price_cny_per_million": str(self.cache_price_cny_per_million),
            "output_price_cny_per_million": str(self.output_price_cny_per_million),
        }


@dataclass
class BudgetLedger:
    limit_cny: Decimal = Decimal("20")
    target_cny: Decimal = Decimal("10")
    prior_committed_cny: Decimal = Decimal("0")
    spent_upper_cny: Decimal = Decimal("0")
    uncertain_upper_cny: Decimal = Decimal("0")
    calls_attempted: int = 0
    calls_completed: int = 0

    def __post_init__(self):
        if self.limit_cny <= 0 or self.target_cny <= 0 or self.target_cny > self.limit_cny or self.prior_committed_cny < 0:
            raise ValueError("Budget target must be positive and no greater than the hard limit")

    @property
    def committed_upper_cny(self) -> Decimal:
        return self.prior_committed_cny + self.spent_upper_cny + self.uncertain_upper_cny

    def preflight_bound(self, payload: dict, config: ProviderConfig) -> Decimal:
        # UTF-8 byte count is intentionally conservative for this small text-only prompt.
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return (
            Decimal(input_bound) * config.input_price_cny_per_million
            + Decimal(config.max_output_tokens) * config.output_price_cny_per_million
        ) / Decimal(1_000_000)

    def authorize(self, bound: Decimal):
        if self.committed_upper_cny + bound > self.limit_cny:
            raise ProviderError("budget_exceeded", "Hard CNY budget would be exceeded; call was not sent")
        self.calls_attempted += 1

    def record_success(self, usage: dict, config: ProviderConfig):
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        cached = int(
            usage.get("prompt_cache_hit_tokens", 0)
            or (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            or 0
        )
        cached = max(0, min(cached, input_tokens))
        miss = input_tokens - cached
        cost = (
            Decimal(miss) * config.input_price_cny_per_million
            + Decimal(cached) * config.cache_price_cny_per_million
            + Decimal(output_tokens) * config.output_price_cny_per_million
        ) / Decimal(1_000_000)
        self.spent_upper_cny += cost
        self.calls_completed += 1
        return {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output_tokens, "cost_upper_cny": str(cost)}

    def record_uncertain(self, bound: Decimal):
        self.uncertain_upper_cny += bound

    def public_dict(self) -> dict:
        return {
            "hard_limit_cny": str(self.limit_cny),
            "target_cny": str(self.target_cny),
            "prior_committed_cny": str(self.prior_committed_cny),
            "run_spent_upper_cny": str(self.spent_upper_cny),
            "run_uncertain_upper_cny": str(self.uncertain_upper_cny),
            "spent_upper_cny": str(self.spent_upper_cny),
            "uncertain_upper_cny": str(self.uncertain_upper_cny),
            "committed_upper_cny": str(self.committed_upper_cny),
            "calls_attempted": self.calls_attempted,
            "calls_completed": self.calls_completed,
            "invoice_cost_verified": False,
        }


def http_post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ProviderError("response_too_large", "Provider response exceeded 2 MB")
            return json.loads(data)
    except urllib.error.HTTPError as error:
        code = "authentication_failed" if error.code in (401, 403) else "provider_http_error"
        retryable = error.code == 429 or 500 <= error.code < 600
        raise ProviderError(code, f"Provider returned HTTP {error.code}", retryable=retryable) from error
    except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
        raise ProviderError("network_error", "Provider request failed or timed out", retryable=True) from error
    except json.JSONDecodeError as error:
        raise ProviderError("invalid_provider_json", "Provider returned invalid envelope JSON") from error


def request_plan(messages: list[dict], config: ProviderConfig, ledger: BudgetLedger, transport=http_post_json) -> dict:
    payload = {
        "model": config.model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": config.max_output_tokens,
        "stream": False,
    }
    if config.official_deepseek:
        payload["thinking"] = {"type": "disabled"}
    elif urlparse(config.base_url).hostname == "api.siliconflow.cn":
        payload["enable_thinking"] = False
    bound = ledger.preflight_bound(payload, config)
    ledger.authorize(bound)
    started = time.perf_counter()
    try:
        envelope = transport(
            config.endpoint,
            payload,
            {"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key},
            config.timeout_seconds,
        )
    except ProviderError:
        ledger.record_uncertain(bound)
        raise
    try:
        choice = envelope["choices"][0]
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise KeyError("empty content")
        usage = ledger.record_success(envelope.get("usage") or {}, config)
        return {
            "content": content,
            "response_id": envelope.get("id"),
            "model_returned": envelope.get("model"),
            "finish_reason": choice.get("finish_reason"),
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except (KeyError, IndexError, TypeError) as error:
        ledger.record_uncertain(bound)
        raise ProviderError("invalid_provider_shape", "Provider response lacked a usable assistant JSON message") from error
