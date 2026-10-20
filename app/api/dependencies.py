from typing import Annotated

from fastapi import Depends, Request

from app.services.llm_client import LLMClient


def get_llm_client(request: Request) -> LLMClient:
    return request.app.state.llm_client


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
