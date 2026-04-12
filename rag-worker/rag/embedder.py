import logging
import os
from typing import List, Optional

log = logging.getLogger(__name__)

_model = None
_model_name: Optional[str] = None


def get_model():
    global _model, _model_name
    name = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")
    if _model is None or _model_name != name:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model: %s", name)
        _model = SentenceTransformer(name)
        _model_name = name
    return _model


def embed(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return [e.tolist() for e in embeddings]


def embedding_dim() -> int:
    model = get_model()
    return model.get_sentence_embedding_dimension()
