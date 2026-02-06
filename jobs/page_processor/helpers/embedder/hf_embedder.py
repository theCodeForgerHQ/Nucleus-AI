from sentence_transformers import SentenceTransformer


def get_model():
    try:
        return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        return None


def embed(texts: list[str]):
    try:
        model = get_model()
        if not model:
            return None

        return (
            model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            .tolist()
        )
    except Exception:
        return None
