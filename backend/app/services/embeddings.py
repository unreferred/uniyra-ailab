"""Embedding provider abstraction for Knowledge collections.

Two REAL providers, no fake embeddings:

* ``LocalHashEmbeddingProvider`` — explicit offline provider based on feature
  hashing (word unigrams + character trigrams hashed into a fixed-dimension
  vector with L2 normalization). The cosine geometry is real, but the
  semantics are far weaker than neural embeddings. Suitable for offline use
  and tests; production deployments should point collections at a neural
  provider.
* ``OpenAICompatibleEmbeddingProvider`` — calls ``{base_url}/embeddings``
  with ``{model, input}`` on any OpenAI-compatible endpoint (BYOK).

Embeddings are plain ``List[float]`` values; callers persist them through
:mod:`app.services.vector_store`.
"""
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional

import httpx

from .. import config
from .. import repository as repo
from ..urlguard import UrlValidationError, async_validate_provider_url

# Upper bound on an embedding HTTP response body (4 MB).
_RESPONSE_CAP_BYTES = 4 * 1024 * 1024

_WORD_RE = re.compile(r"[a-z0-9]+")


class EmbeddingError(ValueError):
    """Raised for embedding failures. Carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def embedding_fingerprint(
    provider: str, model: str, dimension: int, version: str
) -> str:
    """Return the stable compatibility key for an embedding contract."""
    payload = {
        "provider": str(provider or "").strip().lower(),
        "model": str(model or ""),
        "dimension": int(dimension or 0),
        "version": str(version or ""),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_embedding_response(
    vectors: Any, expected_count: int, expected_dimension: Optional[int] = None
) -> List[List[float]]:
    """Validate provider cardinality, dimensions and finite numeric values."""
    if not isinstance(vectors, list) or len(vectors) != int(expected_count):
        raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
    normalized: List[List[float]] = []
    dimension: Optional[int] = None
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
        if vector and dimension is None:
            dimension = len(vector)
        if dimension is not None and len(vector) != dimension:
            raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
        values: List[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
            values.append(number)
        normalized.append(values)
    if expected_dimension and any(len(vector) != int(expected_dimension) for vector in normalized):
        raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
    return normalized


class EmbeddingProvider:
    """Provider contract: embed a batch of texts into fixed-dim vectors."""

    name = "base"
    dimension = 0

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Return one vector per input text, preserving input order."""
        raise NotImplementedError


def _batch_size() -> int:
    try:
        return max(1, int(getattr(config, "EMBEDDING_BATCH_SIZE", 16)))
    except (TypeError, ValueError):
        return 16


def _default_dim() -> int:
    try:
        return max(8, int(getattr(config, "EMBEDDING_DIM", 256)))
    except (TypeError, ValueError):
        return 256


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic offline embeddings via feature hashing.

    Each text contributes word-unigram features (weight 1.0) and
    character-trigram features (weight 0.5). Features are hashed with SHA-256
    into ``dimension`` buckets (stdlib ``hashlib`` only) and the resulting
    vector is L2-normalized, so cosine similarity is meaningful. Empty texts
    yield zero vectors. This is an explicit offline provider: real cosine
    geometry, weaker semantics than neural embeddings.
    """

    name = "local-hashing-v1"

    def __init__(
        self,
        dimension: Optional[int] = None,
        model: Optional[str] = None,
        version: Optional[str] = None,
    ):
        self.dimension = int(dimension) if dimension else _default_dim()
        if self.dimension <= 0:
            self.dimension = _default_dim()
        self.model = str(model or self.name)
        self.name = self.model
        self.version = str(version or getattr(config, "EMBEDDING_VERSION", "embedding-v1"))

    @staticmethod
    def _features(text: str) -> List[tuple]:
        lowered = text.lower()
        feats: List[tuple] = []
        for word in _WORD_RE.findall(lowered):
            feats.append((f"w:{word}", 1.0))
        collapsed = re.sub(r"\s+", " ", lowered).strip()
        if len(collapsed) < 3:
            if collapsed:
                feats.append((f"c:{collapsed}", 0.5))
        else:
            for index in range(len(collapsed) - 2):
                feats.append((f"c:{collapsed[index:index + 3]}", 0.5))
        return feats

    def _vectorize(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        if not text or not text.strip():
            return vec
        for feature, weight in self._features(text):
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            vec[bucket] += weight
        norm = math.sqrt(sum(value * value for value in vec))
        if norm <= 0.0:
            return vec
        return [value / norm for value in vec]

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Hash each text; CPU-cheap pure-stdlib work, no thread offload."""
        out: List[List[float]] = []
        size = _batch_size()
        for start in range(0, len(texts or []), size):
            for text in (texts or [])[start:start + size]:
                out.append(self._vectorize(text or ""))
        return out


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embeddings from any OpenAI-compatible ``/embeddings`` endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: Optional[float] = None,
        dimension: Optional[int] = None,
    ):
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self.model = model or str(getattr(config, "EMBEDDING_MODEL", "embeddings"))
        self.name = self.model
        self.dimension = int(dimension) if dimension else 0
        self.version = str(getattr(config, "EMBEDDING_VERSION", "embedding-v1"))
        try:
            self.timeout = float(timeout if timeout is not None else getattr(config, "EMBEDDING_TIMEOUT_SECONDS", 30))
        except (TypeError, ValueError):
            self.timeout = 30.0

    def _headers(self) -> Dict[str, str]:
        # The API key is used here only; it is never logged or echoed.
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    @staticmethod
    def _parse_vectors(payload: Any) -> List[List[float]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
        vectors: List[List[float]] = []
        dim: Optional[int] = None
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
            raw = item["embedding"]
            if not raw:
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
            vec: List[float] = []
            for value in raw:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
                number = float(value)
                if not math.isfinite(number):
                    raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
                vec.append(number)
            if dim is None:
                dim = len(vec)
            elif len(vec) != dim:
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
            vectors.append(vec)
        if not vectors:
            raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502)
        return vectors

    async def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        try:
            validated = await async_validate_provider_url(self._base_url)
        except UrlValidationError as exc:
            raise EmbeddingError(exc.message, status_code=400) from exc
        url = f"{validated}/embeddings"
        client = httpx.AsyncClient(timeout=self.timeout)
        # Never follow redirects for provider calls (SSRF guard contract).
        client.follow_redirects = False
        try:
            try:
                response = await client.post(url, headers=self._headers(), json={"model": self.model, "input": batch})
            except httpx.TimeoutException as exc:
                raise EmbeddingError("Embedding provider request timed out", status_code=503) from exc
            except httpx.HTTPError as exc:
                raise EmbeddingError("Embedding provider request failed", status_code=503) from exc
            try:
                await response.aread()
            except httpx.HTTPError as exc:
                raise EmbeddingError("Embedding provider request failed", status_code=503) from exc
            if len(response.content or b"") > _RESPONSE_CAP_BYTES:
                raise EmbeddingError("Embedding provider response was too large", status_code=502)
            status = response.status_code
            if status in (401, 403):
                raise EmbeddingError("Embedding provider authentication failed", status_code=401)
            if status == 429:
                raise EmbeddingError("Embedding provider rate limit exceeded", status_code=429)
            if status >= 400:
                raise EmbeddingError("Embedding provider request failed", status_code=502)
            try:
                payload = response.json()
            except Exception as exc:
                raise EmbeddingError("Embedding provider returned an unexpected response", status_code=502) from exc
            vectors = self._parse_vectors(payload)
            return validate_embedding_response(vectors, len(batch), self.dimension or None)
        finally:
            try:
                await client.aclose()
            except Exception:
                pass

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed non-empty texts; zero vectors stand in for empty inputs.

        Alignment with the input order is preserved so callers can zip texts
        with vectors directly.
        """
        texts = list(texts or [])
        if not texts:
            return []
        nonempty_indexes: List[int] = []
        nonempty_texts: List[str] = []
        for index, text in enumerate(texts):
            if text and text.strip():
                nonempty_indexes.append(index)
                nonempty_texts.append(text)
        vectors: List[List[float]] = []
        size = _batch_size()
        for start in range(0, len(nonempty_texts), size):
            vectors.extend(await self._embed_batch(nonempty_texts[start:start + size]))
        vectors = validate_embedding_response(vectors, len(nonempty_texts), self.dimension or None)
        if vectors:
            self.dimension = len(vectors[0])
        dim = self.dimension or (len(vectors[0]) if vectors else 0)
        out: List[List[float]] = [[0.0] * dim for _ in texts]
        for position, vector in zip(nonempty_indexes, vectors):
            out[position] = vector
        return out


async def resolve_embedding_provider(owner_id: str, embedding_config: Optional[Dict[str, Any]]) -> EmbeddingProvider:
    """Resolve the embedding provider snapshot stored on a collection.

    ``embedding_config`` keys: ``provider`` ("local" | "openai_compatible",
    default from config), ``model``, ``dimension``, ``provider_id`` (required
    for ``openai_compatible``: loads the providers doc, requires
    ``provider_type == "openai_compatible"`` + enabled, decrypts
    ``api_key_enc``). The local provider is always available.
    """
    cfg = dict(embedding_config or {})
    provider_name = str(cfg.get("provider") or getattr(config, "EMBEDDING_PROVIDER", "local")).strip().lower()
    model = str(cfg.get("model") or getattr(config, "EMBEDDING_MODEL", "local-hashing-v1"))
    try:
        dimension = int(cfg.get("dimension") or _default_dim())
    except (TypeError, ValueError):
        dimension = _default_dim()

    if provider_name in ("local", "local-hashing", "local_hashing", "hashing"):
        return LocalHashEmbeddingProvider(dimension=dimension, model=model, version=str(cfg.get("version") or getattr(config, "EMBEDDING_VERSION", "embedding-v1")))

    if provider_name in ("openai_compatible", "openai-compatible", "openai"):
        provider_id = cfg.get("provider_id")
        if not provider_id:
            raise EmbeddingError("Embedding provider is not configured", status_code=400)
        doc = await repo.providers.get(str(provider_id), owner_id)
        if not doc:
            raise EmbeddingError("Embedding provider is not configured", status_code=400)
        if doc.get("provider_type") != "openai_compatible" or not doc.get("enabled", True):
            raise EmbeddingError("Embedding provider is unavailable", status_code=503)
        base_url = doc.get("base_url")
        api_key_enc = doc.get("api_key_enc")
        if not base_url or not api_key_enc:
            raise EmbeddingError("Embedding provider is not configured", status_code=400)
        try:
            from ..security import decrypt_secret
            api_key = decrypt_secret(api_key_enc)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("Embedding provider credentials could not be used", status_code=502) from exc
        if not api_key:
            raise EmbeddingError("Embedding provider is not configured", status_code=400)
        try:
            timeout = float(getattr(config, "EMBEDDING_TIMEOUT_SECONDS", 30))
        except (TypeError, ValueError):
            timeout = 30.0
        provider = OpenAICompatibleEmbeddingProvider(
            base_url=base_url, api_key=api_key, model=model, timeout=timeout, dimension=dimension
        )
        provider.version = str(cfg.get("version") or getattr(config, "EMBEDDING_VERSION", "embedding-v1"))
        return provider

    raise EmbeddingError("Unknown embedding provider", status_code=400)
