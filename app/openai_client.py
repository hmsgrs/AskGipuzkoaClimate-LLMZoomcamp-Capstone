"""OpenAI embedding client with model and dimension validation."""

from openai import OpenAI

from app.db_init import embedding_dimensions, embedding_model


class OpenAIEmbeddingClient:
    def __init__(self, client=None, model: str | None = None, dimensions: int | None = None):
        self.client = client or OpenAI()
        self.model = model or embedding_model()
        self.dimensions = dimensions or embedding_dimensions()

    def embed(self, texts: list[str]):
        if not texts:
            return []
        response = self.client.embeddings.create(
            input=texts,
            model=self.model,
            dimensions=self.dimensions,
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI returned a different number of embeddings than inputs")
        if any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError(
                f"OpenAI embedding dimension does not match {self.dimensions}"
            )
        return vectors
