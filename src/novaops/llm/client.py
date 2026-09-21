"""The single model seam.

Every model call in this system goes through this module, so that swapping providers —
or pinning a different model for one workflow — touches one file. The specification asks
for this directly: "Keep model calls behind one small function so a provider swap touches
one place."

It stays small on purpose. Context selection, tool routing, memory and scoring all live
where they are taught; what belongs here is only the provider-facing seam.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_aws import ChatBedrockConverse

from novaops import config


def get_model(**overrides) -> ChatBedrockConverse:
    """The shared Bedrock chat model.

    `temperature=0` throughout: run-to-run drift makes an evaluation set unreadable, and
    every claim this system makes is supposed to come from evidence rather than from
    sampling.
    """

    params = {
        "model": config.BEDROCK_MODEL_ID,
        "region_name": config.AWS_REGION,
        "temperature": 0,
    }
    params.update(overrides)
    return ChatBedrockConverse(**params)


# Titan Text Embeddings V2 default width. The index is built at this width and queried
# at this width; they cannot differ.
EMBED_DIM = 1024


@lru_cache(maxsize=1)
def _bedrock_runtime():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


def embed_text(text: str) -> list[float]:
    """One embedding call.

    `normalize=True` matters: it makes every vector unit length, which is what lets a
    FAISS inner-product index behave as exact cosine similarity. The index and the query
    must use the same model and the same setting, or the geometry is meaningless and the
    failure is silently poor recall rather than an error.
    """

    import json

    response = _bedrock_runtime().invoke_model(
        modelId=config.BEDROCK_EMBED_MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True}),
    )
    return json.loads(response["body"].read())["embedding"]
