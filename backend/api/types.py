from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

class ChatRequest(BaseModel):
    # Only the question is accepted now
    question: str = Field(..., min_length=1, max_length=2000)
    # Pydantic v2 style (replaces `class Config: extra = "ignore"`)
    model_config = ConfigDict(extra="ignore")

class Citation(BaseModel):
    id: str
    text: Optional[str] = None

class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int | None = None

class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation]
    usage: Usage
    llm_called: bool
    input_token_limit: Optional[int] = None
    model: Optional[str] = None
