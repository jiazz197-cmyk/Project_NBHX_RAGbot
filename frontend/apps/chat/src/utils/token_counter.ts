import { encodingForModel, getEncoding, type TiktokenModel } from 'js-tiktoken'

const FALLBACK_ENCODING = 'cl100k_base'
const DEFAULT_MODEL = 'gpt-4o-mini'

let encoder: ReturnType<typeof getEncoding> | null = null

const initEncoder = (): ReturnType<typeof getEncoding> => {
  if (encoder) {
    return encoder
  }

  try {
    encoder = encodingForModel(DEFAULT_MODEL as TiktokenModel)
    return encoder
  } catch {
    encoder = getEncoding(FALLBACK_ENCODING)
    return encoder
  }
}

export const countTokens = (text: string): number => {
  const normalized = text || ''
  if (!normalized) {
    return 0
  }

  try {
    return initEncoder().encode(normalized).length
  } catch {
    return Math.ceil(normalized.length * 1.2)
  }
}
