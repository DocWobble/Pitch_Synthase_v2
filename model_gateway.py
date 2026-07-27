import asyncio
import os
from openai import AsyncOpenAI

MODEL_ROUTES: dict[str, str] = {
    "anchor":   "gpt-5.4",
    "draft":    "gpt-5.4",
    "quality":  "gpt-5.4",
    "classify": "gpt-4.1-nano",
    "image":    "gpt-image-2",
}


class ModelGateway:
    """[gateway]: single boundary for all OpenAI API calls.

    Workers call gateway.model_for("anchor") instead of a hardcoded constant.
    Swapping models or adding fallback routing only requires editing MODEL_ROUTES.
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._img_sem = asyncio.Semaphore(4)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=360.0)
        return self._client

    def model_for(self, task: str) -> str:
        return MODEL_ROUTES[task]

    @property
    def chat(self):
        return self._get_client().chat

    @property
    def responses(self):
        return self._get_client().responses

    async def images_generate(self, **kwargs):
        async with self._img_sem:
            return await self._get_client().images.generate(**kwargs)


gateway = ModelGateway()
