export interface ChatRequest {
  question: string
}

export interface Citation {
  id: string
  text?: string
}

// If you use api.ts's toCamel(), these remain camelCase.
// index.tsx reads snake_case directly from the API response.
export interface Usage {
  inputTokens: number
  outputTokens: number
  thoughtsTokens?: number
}

export interface ChatResponse {
  answer: string
  citations?: Citation[]
  usage?: Usage
  llmCalled?: boolean
}
