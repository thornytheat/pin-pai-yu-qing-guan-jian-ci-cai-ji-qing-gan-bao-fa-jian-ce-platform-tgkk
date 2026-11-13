import { defineStore } from 'pinia'
import {
  fetchDraftDiff,
  fetchRuleHistory,
  fetchRulesetDraft,
  fetchRulesetOverview,
  publishDraft,
  saveRulesetDraft,
  trialRuleset,
  type DraftPayload,
  type RulePatch,
} from '../api/ruleset'
import type { DiffResult, PublishResult, RuleHistory, RulesetDraft, RulesetOverview } from '../types/domain'

interface RulesetState {
  overview: RulesetOverview | null
  history: RuleHistory | null
  draft: RulesetDraft | null
  diff: DiffResult | null
  publishResult: PublishResult | null
  loading: boolean
  saving: boolean
  publishing: boolean
  error: string
  flash: string
}

export const useRulesetStore = defineStore('ruleset', {
  state: (): RulesetState => ({
    overview: null,
    history: null,
    draft: null,
    diff: null,
    publishResult: null,
    loading: false,
    saving: false,
    publishing: false,
    error: '',
    flash: '',
  }),
  getters: {
    draftCount: (state): number => state.overview?.drafts.filter((item) => item.status === 'draft').length ?? 0,
    aligned: (state): boolean => state.overview?.alignment.aligned ?? false,
  },
  actions: {
    async load() {
      this.loading = true
      this.error = ''
      try {
        this.overview = await fetchRulesetOverview()
        if (!this.draft && this.overview.drafts.length) {
          const first = this.overview.drafts.find((item) => item.status === 'draft') ?? this.overview.drafts[0]
          await this.select(first.id)
        }
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '规则版本加载失败'
      } finally {
        this.loading = false
      }
    },
    async loadHistory() {
      try {
        this.history = await fetchRuleHistory()
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '版本历史加载失败'
      }
    },
    async select(draftId: string) {
      this.diff = null
      this.publishResult = null
      try {
        this.draft = await fetchRulesetDraft(draftId)
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '草稿加载失败'
      }
    },
    async save(payload: DraftPayload) {
      this.saving = true
      this.error = ''
      try {
        const draft = await saveRulesetDraft(payload)
        this.draft = draft
        this.publishResult = null
        this.flash = `草稿已保存（${draft.items.length} 组快照），尚未影响采集与预警`
        await this.load()
        return draft
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '草稿保存失败'
        return null
      } finally {
        this.saving = false
      }
    },
    async runDiff(draftId?: string) {
      const target = draftId ?? this.draft?.id
      if (!target) return null
      this.error = ''
      try {
        this.diff = await fetchDraftDiff(target)
        return this.diff
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '对照预演失败'
        return null
      }
    },
    async trial(payload: DraftPayload) {
      this.error = ''
      try {
        this.diff = await trialRuleset(payload)
        return this.diff
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '对照预演失败'
        return null
      }
    },
    async publish(reason: string) {
      if (!this.draft) return null
      this.publishing = true
      this.error = ''
      try {
        const result = await publishDraft(this.draft.id, reason)
        this.publishResult = result
        this.draft = result.draft
        this.flash = result.idempotent
          ? `同一草稿已发布过，复用版本 ${JSON.stringify(result.versions)}（未新建版本、未重复统计）`
          : `已发布：${Object.entries(result.versions)
              .map(([code, version]) => `${code} v${version}`)
              .join(' · ')}`
        this.overview = result.overview
        await this.loadHistory()
        return result
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '发布失败，已保留原版本'
        return null
      } finally {
        this.publishing = false
      }
    },
    clearFlash() {
      this.flash = ''
    },
  },
})

export type { RulePatch }
