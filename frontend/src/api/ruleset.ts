import http from './http'
import type {
  DiffResult,
  PublishResult,
  RuleHistory,
  RulesetDraft,
  RulesetOverview,
} from '../types/domain'

export interface RulePatch {
  group_id: string
  keywords?: string[]
  synonyms?: string[]
  excludes?: string[]
  z_threshold?: number
  min_count?: number
  enabled?: boolean
}

export interface DraftPayload {
  label?: string
  changes: RulePatch[]
}

export async function fetchRulesetOverview(): Promise<RulesetOverview> {
  const { data } = await http.get<RulesetOverview>('/rulesets/overview')
  return data
}

export async function saveRulesetDraft(payload: DraftPayload): Promise<RulesetDraft> {
  const { data } = await http.post<RulesetDraft>('/rulesets/drafts', payload)
  return data
}

export async function fetchRulesetDraft(draftId: string): Promise<RulesetDraft> {
  const { data } = await http.get<RulesetDraft>(`/rulesets/drafts/${draftId}`)
  return data
}

export async function fetchDraftDiff(draftId: string): Promise<DiffResult> {
  const { data } = await http.post<DiffResult>(`/rulesets/drafts/${draftId}/diff`, {})
  return data
}

export async function trialRuleset(payload: DraftPayload): Promise<DiffResult> {
  const { data } = await http.post<DiffResult>('/rulesets/trial', payload)
  return data
}

export async function publishDraft(
  draftId: string,
  reason = '',
): Promise<PublishResult & { draft: RulesetDraft; overview: RulesetOverview }> {
  const { data } = await http.post(`/rulesets/drafts/${draftId}/publish`, { reason })
  return data
}

export async function fetchRuleHistory(): Promise<RuleHistory> {
  const { data } = await http.get<RuleHistory>('/rulesets/history')
  return data
}
