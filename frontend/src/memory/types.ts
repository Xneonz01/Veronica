// Shared types for the Memory page — mirror the backend Pydantic rows.

export type MemoryKind = 'profile' | 'fact' | 'preference' | 'episodic' | 'summary'
export type LearningMode = 'proactive' | 'review_only'

export interface MemoryRow {
  id: number
  kind: MemoryKind
  key: string | null
  content: string
  importance: number
  confidence: number
  pinned: boolean
  pending: boolean
  source: string
  created_at: string
  updated_at: string
  last_accessed_at: string | null
  access_count: number
}

export interface DocumentRow {
  id: number
  title: string
  source_path: string | null
  mime: string
  chunk_count: number
  added_at: string
}
