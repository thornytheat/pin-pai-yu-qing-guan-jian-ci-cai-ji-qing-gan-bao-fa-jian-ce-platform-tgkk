<template>
  <div class="page">
    <PageHeader
      kicker="keyword groups · synonyms · excludes"
      title="关键词组与词典"
      :description="`${store.totals.groups} 组关键词覆盖 ${meta.brands.length} 个品牌。修改先存草稿，用同一批候选新旧对照后再一次性发布；线上版本 ${liveLabel}，历史预警保留原版本。`"
    >
      <template #actions>
        <button v-if="canWrite" class="btn" data-test="new-group" @click="startCreate">新建组</button>
        <button v-if="canTrial" class="btn" :disabled="!form.keywordsText.trim()" data-test="trial" @click="runTrial">试算召回</button>
        <button v-if="canWrite" class="btn" :disabled="ruleset.saving" data-test="save-draft" @click="saveDraft">存为草稿</button>
        <button
          v-if="canTrial"
          class="btn"
          :disabled="!ruleset.draft || ruleset.loading"
          data-test="diff-preview"
          @click="previewDiff"
        >新旧对照预演</button>
        <button
          v-if="canPublish"
          class="btn btn--amber"
          :disabled="!ruleset.draft || ruleset.draft.status === 'published' || ruleset.publishing"
          data-test="publish-draft"
          @click="publish"
        >{{ ruleset.publishing ? '发布中…' : '一次性发布' }}</button>
      </template>
    </PageHeader>

    <ErrorNotice v-if="store.error" :message="store.error" :retry="false" @retry="() => {}" />
    <ErrorNotice v-if="ruleset.error" :message="ruleset.error" :retry="false" @retry="() => {}" />
    <div v-if="ruleset.flash || store.flash" class="flash rise">{{ ruleset.flash || store.flash }}</div>

    <div class="grid">
      <aside class="list">
        <div class="list__head">
          <span class="eyebrow">词组清单</span>
          <span class="mono list__meta">{{ store.enabledCount }}/{{ store.items.length }} 启用</span>
        </div>
        <button
          v-for="group in store.items"
          :key="group.id"
          class="item"
          :class="{ 'is-on': group.id === pendingId }"
          :data-test="`group-${group.code}`"
          @click="selectGroup(group.id)"
        >
          <span class="item__brand mono">{{ group.brand_label }}</span>
          <span class="item__name">{{ group.name }}</span>
          <span class="item__stats mono">
            {{ group.stats?.mentions ?? 0 }} 条 · 负 {{ pct(group.stats?.neg_rate ?? 0) }}
          </span>
          <span v-if="!group.enabled" class="item__off mono">停用</span>
        </button>
      </aside>

      <section class="editor">
        <div class="editor__head">
          <div>
            <div class="eyebrow">{{ creating ? 'new group' : `${form.brandLabel} · z>${form.z_threshold}` }}</div>
            <h2 class="editor__title">{{ creating ? '新建关键词组' : form.name || '（未命名）' }}</h2>
          </div>
          <span class="mono editor__code">{{ creating ? form.code || 'draft' : form.code }}</span>
        </div>

        <div class="livebar">
          <span class="livebar__tag mono">线上 v{{ liveVersion || '—' }}</span>
          <span class="livebar__meta mono">
            {{ liveStartedAt ? `自 ${liveStartedAt}` : '尚未发布' }}
            <em v-if="livePublisher"> · {{ livePublisher }}</em>
          </span>
          <span class="livebar__draft mono" :class="{ 'is-dirty': dirty }">
            {{ dirty ? '有未发布的改动' : '与线上一致' }}
          </span>
        </div>

        <div class="form-grid">
          <label class="fld">
            <span class="label">组名</span>
            <input v-model="form.name" class="field" :disabled="!canWrite || !creating" data-test="group-name" />
          </label>
          <label class="fld">
            <span class="label">品牌</span>
            <select v-model="form.brand_key" class="field" :disabled="!canWrite || !creating">
              <option v-for="brand in meta.brands" :key="brand.key" :value="brand.key">{{ brand.name }}</option>
            </select>
          </label>
          <label v-if="creating" class="fld">
            <span class="label">编码（小写英文/短横线）</span>
            <input v-model="form.code" class="field" placeholder="coffee-newtopic" :disabled="!canWrite" />
          </label>
          <label class="fld">
            <span class="label">激活</span>
            <button
              class="toggle"
              :class="{ 'is-on': form.enabled }"
              :disabled="!canWrite"
              @click="form.enabled = !form.enabled"
            >{{ form.enabled ? '启用中' : '已停用' }}</button>
          </label>
        </div>

        <div class="tags">
          <label class="fld fld--wide">
            <span class="label">主关键词（每行一个）</span>
            <textarea v-model="form.keywordsText" class="field tags__area" rows="4" :disabled="!canWrite" data-test="keywords" />
          </label>
          <label class="fld fld--wide">
            <span class="label">同义词 / 谐音 / 品牌简写（扩召回）</span>
            <textarea v-model="form.synonymsText" class="field tags__area" rows="4" :disabled="!canWrite" data-test="synonyms" />
          </label>
          <label class="fld fld--wide">
            <span class="label">排除词（去噪声）</span>
            <textarea v-model="form.excludesText" class="field tags__area" rows="3" :disabled="!canWrite" data-test="excludes" />
          </label>
        </div>

        <div class="form-grid form-grid--tight">
          <label class="fld">
            <span class="label">z 阈值</span>
            <input v-model.number="form.z_threshold" class="field mono" type="number" step="0.1" min="1" max="8" :disabled="!canWrite" />
          </label>
          <label class="fld">
            <span class="label">最小量 x</span>
            <input v-model.number="form.min_count" class="field mono" type="number" min="1" max="50" :disabled="!canWrite" />
          </label>
          <label class="fld">
            <span class="label">优先级</span>
            <input v-model.number="form.priority" class="field mono" type="number" min="0" max="10" :disabled="!canWrite" />
          </label>
          <label class="fld fld--wide">
            <span class="label">值班备注</span>
            <input v-model="form.owner_note" class="field" :disabled="!canWrite" />
          </label>
        </div>

        <section v-if="store.trial" class="trial">
          <div class="trial__head">
            <span class="eyebrow">试算召回预览</span>
            <span class="mono trial__meta">
              扫描 {{ store.trial.scanned }} · 命中 {{ store.trial.matched }} · 保留 {{ store.trial.kept }} · 排除 {{ store.trial.excluded }}
            </span>
          </div>
          <div class="trial__bars">
            <div class="trial__bar">
              <span class="label">召回率</span>
              <span class="trial__track"><span class="trial__fill" :style="{ width: pct(store.trial.recall_rate) }" /></span>
              <span class="mono">{{ pct(store.trial.recall_rate) }}</span>
            </div>
            <div class="trial__bar">
              <span class="label">命中精度</span>
              <span class="trial__track"><span class="trial__fill trial__fill--pos" :style="{ width: pct(store.trial.precision_hint) }" /></span>
              <span class="mono">{{ pct(store.trial.precision_hint) }}</span>
            </div>
          </div>
          <ul class="trial__samples">
            <li v-for="sample in store.trial.samples" :key="sample.external_id" class="trial__sample">
              <span class="mono trial__src">{{ sample.source_code }}</span>
              <span class="trial__title">{{ sample.title }}</span>
              <span class="mono trial__hits">
                {{ [...sample.hit_keywords, ...sample.hit_synonyms].join(' / ') || '—' }}
                <em v-if="sample.hit_excludes.length">⊘ {{ sample.hit_excludes.join(' / ') }}</em>
              </span>
            </li>
          </ul>
          <p class="trial__note">{{ store.trial.corpus_note }}</p>
        </section>

        <section v-if="ruleset.diff" class="preview">
          <RulesetDiffPanel :result="ruleset.diff" />
          <div v-if="canPublish" class="preview__actions">
            <label class="fld fld--wide">
              <span class="label">发布说明（写入审计）</span>
              <input v-model="publishReason" class="field" placeholder="如：补充「塑料片」同义词，z 阈值不变" data-test="publish-reason" />
            </label>
            <button
              class="btn btn--amber"
              :disabled="!ruleset.draft || ruleset.draft.status === 'published' || ruleset.publishing"
              data-test="publish-confirm"
              @click="publish"
            >确认一次性发布 {{ changedCount }} 组</button>
          </div>
        </section>

        <section v-if="ruleset.publishResult" class="publish" data-test="publish-result">
          <div class="publish__head">
            <span class="eyebrow">发布结果</span>
            <span class="mono publish__meta">{{ ruleset.publishResult.published_at?.slice(0, 19) }}</span>
          </div>
          <p class="publish__line">
            {{ ruleset.publishResult.idempotent ? '同一草稿重复发布：复用已有版本，未新建版本、未重复统计。' : '已一次性发布；采集、提及归属、情感汇总与爆发预警现在都使用同一版本。' }}
          </p>
          <ul class="publish__versions">
            <li
              v-for="(version, code) in ruleset.publishResult.versions"
              :key="code"
              class="mono"
              :class="{ 'is-unchanged': hasKey(ruleset.publishResult.unchanged_versions, code) && !hasKey(ruleset.publishResult.new_versions, code) }"
            >
              {{ code }} → v{{ version }}
              <em v-if="hasKey(ruleset.publishResult.new_versions, code)">新版本</em>
              <em v-else>未变，沿用</em>
            </li>
          </ul>
          <p class="publish__note">历史提及与历史预警保留原版本（只对发布之后的新提及生效）。</p>
        </section>

        <section class="drafts">
          <div class="drafts__head">
            <span class="eyebrow">草稿与线上版本</span>
            <span class="mono drafts__meta">
              草稿 {{ ruleset.draftCount }} · 归属对齐 {{ ruleset.overview?.alignment.aligned ? '是' : '否' }}
            </span>
          </div>
          <div class="drafts__cols">
            <ul class="drafts__list">
              <li
                v-for="draft in ruleset.overview?.drafts ?? []"
                :key="draft.id"
                class="draft"
                :class="{ 'is-on': draft.id === ruleset.draft?.id }"
                @click="selectDraft(draft.id)"
              >
                <span class="draft__label">{{ draft.label }}</span>
                <span class="mono draft__meta">
                  {{ draft.status === 'published' ? '已发布' : '草稿' }} · {{ draft.items.length }} 组 ·
                  {{ draft.updated_at?.slice(5, 16).replace('T', ' ') }}
                </span>
              </li>
              <li v-if="!(ruleset.overview?.drafts ?? []).length" class="draft draft--empty">还没有草稿：改完点「存为草稿」</li>
            </ul>
            <div class="alignbox">
              <div class="alignbox__row">
                <span class="label">四环节同一口径</span>
                <span class="mono" :class="ruleset.overview?.alignment.aligned ? 'ok' : 'bad'">
                  {{ ruleset.overview?.alignment.aligned ? '已对齐' : '存在偏差' }}
                </span>
              </div>
              <div class="alignbox__row mono">
                提及 {{ ruleset.overview?.alignment.attributed ?? 0 }}/{{ ruleset.overview?.alignment.mentions ?? 0 }}
                · 保留 {{ ruleset.overview?.alignment.kept ?? 0 }}
              </div>
              <div class="alignbox__row mono">
                预警版本 {{ (ruleset.overview?.alignment.alert_versions ?? []).join(', ') || '—' }}
              </div>
              <div class="alignbox__row mono">
                候选批次 {{ ruleset.overview?.batch.candidates ?? 0 }} 条
                （窗口 {{ ruleset.overview?.batch.inbox ?? 0 }} / 库内 {{ ruleset.overview?.batch.stored ?? 0 }}）
              </div>
            </div>
          </div>
        </section>

        <section v-if="ruleset.history" class="history" data-test="rule-history">
          <div class="drafts__head">
            <span class="eyebrow">版本历史（可追溯）</span>
            <span class="mono drafts__meta">最近 {{ ruleset.history.items.length }} 条 · 发布 {{ ruleset.history.publishes.length }} 次</span>
          </div>
          <ul class="hist">
            <li v-for="item in ruleset.history.items.slice(0, 10)" :key="item.id" class="hist__row">
              <span class="mono hist__ver">v{{ item.version }}</span>
              <span class="hist__code mono">{{ item.code }}</span>
              <span class="hist__meta mono">
                {{ item.started_at?.slice(0, 16).replace('T', ' ') }}
                <em v-if="item.is_current" class="hist__cur">线上</em>
                <em v-else-if="item.ended_at">被 v{{ item.parent_version ? item.version + 1 : item.version }} 取代</em>
              </span>
              <span class="hist__words">
                {{ item.keywords.join(' / ') }}
                <em v-if="item.excludes.length">⊘ {{ item.excludes.join(' / ') }}</em>
              </span>
            </li>
          </ul>
          <ul class="hist hist--log">
            <li v-for="log in ruleset.history.publishes.slice(0, 6)" :key="log.id" class="hist__row">
              <span class="mono" :class="log.outcome === 'failed' ? 'bad' : 'ok'">
                {{ log.outcome === 'failed' ? '失败' : '成功' }}
              </span>
              <span class="hist__code mono">{{ log.content_hash.slice(0, 10) }}</span>
              <span class="hist__meta mono">{{ log.created_at?.slice(0, 16).replace('T', ' ') }} · {{ log.actor_email }}</span>
              <span class="hist__words">{{ log.outcome_text }}</span>
            </li>
          </ul>
        </section>

        <section class="dictionary">
          <div class="dict__head">
            <span class="eyebrow">情感词典（只读 · 后端唯一事实源）</span>
            <span class="mono dict__meta">
              正 {{ dictionary?.sizes.positive ?? '—' }} · 负 {{ dictionary?.sizes.negative ?? '—' }} · 程度
              {{ dictionary?.sizes.degree ?? '—' }} · 否定 {{ dictionary?.sizes.negator ?? '—' }}
            </span>
          </div>
          <div v-if="dictionary" class="dict__cols">
            <div>
              <div class="label">正面词 top</div>
              <div class="dict__words">
                <span v-for="word in dictionary.positive.slice(0, 12)" :key="word.word" class="mini">{{ word.word }}<em>{{ word.weight }}</em></span>
              </div>
            </div>
            <div>
              <div class="label">负面词 top</div>
              <div class="dict__words">
                <span v-for="word in dictionary.negative.slice(0, 12)" :key="word.word" class="mini mini--neg">{{ word.word }}<em>{{ word.weight }}</em></span>
              </div>
            </div>
            <div>
              <div class="label">否定 / 反问</div>
              <div class="dict__words">
                <span v-for="word in dictionary.negators.slice(0, 8)" :key="word" class="mini mini--ex">{{ word }}</span>
                <span v-for="word in dictionary.rhetorical.slice(0, 4)" :key="word" class="mini mini--syn">{{ word }}</span>
              </div>
            </div>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import PageHeader from '../components/common/PageHeader.vue'
import ErrorNotice from '../components/common/ErrorNotice.vue'
import RulesetDiffPanel from '../components/domain/RulesetDiffPanel.vue'
import { useGroupStore } from '../stores/groupStore'
import { useRulesetStore } from '../stores/rulesetStore'
import { useMetaStore } from '../stores/metaStore'
import { useAuthStore } from '../stores/authStore'
import { fetchDictionary } from '../api/meta'
import type { RulePatch } from '../api/ruleset'
import type { DictionaryPayload } from '../types/domain'
import { pct } from '../utils/format'

const store = useGroupStore()
const ruleset = useRulesetStore()
const meta = useMetaStore()
const auth = useAuthStore()

const canWrite = computed(() => auth.can('groups:write'))
const canTrial = computed(() => auth.can('groups:trial'))
const canPublish = computed(() => auth.can('groups:publish'))
const publishReason = ref('')

const liveGroup = computed(() => {
  if (creating.value || !form.id) return null
  return ruleset.overview?.groups.find((item) => item.group_id === form.id) ?? null
})
const liveVersion = computed(() => liveGroup.value?.live_version ?? 0)
const liveStartedAt = computed(() => liveGroup.value?.live_started_at?.slice(0, 16).replace('T', ' ') ?? '')
const livePublisher = computed(() => liveGroup.value?.live_published_by ?? '')
const liveLabel = computed(() => {
  const versions = ruleset.overview?.groups.map((item) => `v${item.live_version}`) ?? []
  return versions.length ? versions.join(' / ') : '—'
})

const draftRule = computed(() => ({
  keywords: lines(form.keywordsText),
  synonyms: lines(form.synonymsText),
  excludes: lines(form.excludesText),
  z_threshold: form.z_threshold,
  min_count: form.min_count,
  enabled: form.enabled,
}))

const dirty = computed(() => {
  if (!liveGroup.value || creating.value) return false
  const rule = liveGroup.value.rule
  return (
    JSON.stringify(rule.keywords) !== JSON.stringify(draftRule.value.keywords) ||
    JSON.stringify(rule.synonyms) !== JSON.stringify(draftRule.value.synonyms) ||
    JSON.stringify(rule.excludes) !== JSON.stringify(draftRule.value.excludes) ||
    rule.z_threshold !== draftRule.value.z_threshold ||
    rule.min_count !== draftRule.value.min_count ||
    rule.enabled !== draftRule.value.enabled
  )
})

const changedCount = computed(() => {
  const result = ruleset.diff
  if (!result) return 0
  const olds = new Map(result.rules.old.map((item) => [item.group_id, item.hash]))
  return result.rules.new.filter((item) => olds.get(item.group_id) !== item.hash).length
})
const dictionary = ref<DictionaryPayload | null>(null)
const creating = ref(false)
const pendingId = computed(() => store.selectedId)

const form = reactive({
  id: '',
  code: '',
  name: '',
  brand_key: 'coffee',
  brandLabel: '',
  keywordsText: '',
  synonymsText: '',
  excludesText: '',
  enabled: true,
  priority: 5,
  z_threshold: 3,
  min_count: 5,
  owner_note: '',
})

function lines(text: string): string[] {
  return text
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function syncForm() {
  const group = store.selected
  if (!group) return
  creating.value = false
  form.id = group.id
  form.code = group.code
  form.name = group.name
  form.brand_key = group.brand_key
  form.brandLabel = group.brand_label
  form.keywordsText = group.keywords.join('\n')
  form.synonymsText = group.synonyms.join('\n')
  form.excludesText = group.excludes.join('\n')
  form.enabled = group.enabled
  form.priority = group.priority
  form.z_threshold = group.z_threshold
  form.min_count = group.min_count
  form.owner_note = group.owner_note
}

function selectGroup(id: string) {
  store.select(id)
  syncForm()
}

function startCreate() {
  creating.value = true
  form.id = ''
  form.code = ''
  form.name = ''
  form.brand_key = 'coffee'
  form.brandLabel = meta.brandLabel('coffee')
  form.keywordsText = ''
  form.synonymsText = ''
  form.excludesText = ''
  form.enabled = true
  form.priority = 5
  form.z_threshold = 3
  form.min_count = 5
  form.owner_note = ''
}

function currentChanges(): RulePatch[] {
  if (!form.id) return []
  return [
    {
      group_id: form.id,
      keywords: lines(form.keywordsText),
      synonyms: lines(form.synonymsText),
      excludes: lines(form.excludesText),
      z_threshold: form.z_threshold,
      min_count: form.min_count,
      enabled: form.enabled,
    },
  ]
}

async function saveDraft() {
  if (creating.value) {
    await store.create({
      code: form.code,
      name: form.name,
      brand_key: form.brand_key,
      keywords: lines(form.keywordsText),
      synonyms: lines(form.synonymsText),
      excludes: lines(form.excludesText),
      enabled: form.enabled,
      priority: form.priority,
      z_threshold: form.z_threshold,
      min_count: form.min_count,
      owner_note: form.owner_note,
    })
    creating.value = false
    await ruleset.load()
    syncForm()
    return
  }
  if (!form.id) return
  await ruleset.save({
    label: `${form.name} ${new Date().toISOString().slice(5, 16).replace('T', ' ')}`,
    changes: currentChanges(),
  })
}

async function previewDiff() {
  await ruleset.runDiff()
}

async function publish() {
  const result = await ruleset.publish(publishReason.value || '发布关键词规则修改')
  if (result) {
    publishReason.value = ''
    await store.load()
    syncForm()
  }
}

function hasKey(record: Record<string, number> | undefined, key: string): boolean {
  return Boolean(record && key in record)
}

async function selectDraft(draftId: string) {
  await ruleset.select(draftId)
  const target = ruleset.draft?.items.find((item) => item.group_id === form.id)
  if (target) {
    form.keywordsText = target.rule.keywords.join('\n')
    form.synonymsText = target.rule.synonyms.join('\n')
    form.excludesText = target.rule.excludes.join('\n')
    form.z_threshold = target.rule.z_threshold
    form.min_count = target.rule.min_count
    form.enabled = target.rule.enabled
  }
  if (ruleset.draft?.status === 'draft' && ruleset.draft.last_trial) {
    ruleset.diff = ruleset.draft.last_trial
  }
}

async function runTrial() {
  await store.trialRun({
    code: form.code || 'draft',
    keywords: lines(form.keywordsText),
    synonyms: lines(form.synonymsText),
    excludes: lines(form.excludesText),
  })
}

onMounted(async () => {
  await meta.load()
  await store.load()
  await ruleset.load()
  await ruleset.loadHistory()
  syncForm()
  try {
    dictionary.value = await fetchDictionary()
  } catch {
    dictionary.value = null
  }
})
</script>

<style scoped>
.livebar {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 12px;
  border: 1px solid var(--rule-soft);
  border-left: 2px solid var(--amber-dim);
  background: color-mix(in oklch, var(--ground-2) 45%, var(--ground));
  padding: 6px 9px;
}
.livebar__tag {
  font-size: 10px;
  color: var(--amber);
}
.livebar__meta {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.livebar__meta em {
  font-style: normal;
}
.livebar__draft {
  margin-left: auto;
  font-size: 9.5px;
  color: var(--ink-faint);
}
.livebar__draft.is-dirty {
  color: var(--amber);
}
.preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview__actions {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}
.preview__actions .fld {
  flex: 1 1 280px;
}
.publish {
  border: 1px solid var(--rule);
  border-left: 2px solid var(--pos);
  padding: 9px 12px;
  background: color-mix(in oklch, var(--ground-2) 55%, var(--ground));
}
.publish__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.publish__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.publish__line {
  font-size: 11px;
  color: var(--ink-soft);
  margin: 6px 0;
}
.publish__versions {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 0;
  padding: 0;
}
.publish__versions li {
  font-size: 9.5px;
  border: 1px solid var(--rule-soft);
  padding: 1px 6px;
  color: var(--amber);
}
.publish__versions li.is-unchanged {
  color: var(--ink-faint);
}
.publish__versions li em {
  font-style: normal;
  margin-left: 4px;
  font-size: 8.5px;
  color: var(--ink-faint);
}
.publish__note {
  font-size: 9px;
  color: var(--ink-faint);
  margin: 7px 0 0;
}
.drafts,
.history {
  border-top: 1px solid var(--rule);
  padding-top: 9px;
}
.drafts__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 7px;
}
.drafts__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.drafts__cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 14px;
}
.drafts__list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 168px;
  overflow: auto;
}
.draft {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  padding: 4px 7px;
  border-bottom: 1px solid var(--rule-soft);
  border-left: 2px solid transparent;
  cursor: pointer;
}
.draft:hover {
  background: color-mix(in oklch, var(--ground-3) 50%, var(--ground));
}
.draft.is-on {
  border-left-color: var(--amber);
  background: color-mix(in oklch, var(--ground-3) 75%, var(--ground));
}
.draft__label {
  font-size: 11.5px;
  color: var(--ink);
}
.draft__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.draft--empty {
  font-size: 10px;
  color: var(--ink-faint);
  cursor: default;
}
.alignbox {
  border: 1px solid var(--rule-soft);
  padding: 7px 9px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.alignbox__row {
  font-size: 9.5px;
  color: var(--ink-faint);
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.ok {
  color: var(--pos);
}
.bad {
  color: var(--neg);
}
.hist {
  list-style: none;
  margin: 0;
  padding: 0;
}
.hist__row {
  display: grid;
  grid-template-columns: 42px 104px minmax(0, 1fr);
  gap: 0 8px;
  padding: 3px 0;
  border-bottom: 1px solid var(--rule-soft);
  font-size: 9.5px;
  color: var(--ink-faint);
}
.hist__ver {
  color: var(--amber-dim);
}
.hist__code {
  color: var(--ink-soft);
}
.hist__meta em {
  font-style: normal;
  margin-left: 6px;
}
.hist__cur {
  color: var(--pos);
}
.hist__words {
  grid-column: 3;
  font-size: 9px;
  color: var(--ink-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist__words em {
  font-style: normal;
  color: var(--neg);
  margin-left: 6px;
}
.hist--log {
  margin-top: 7px;
}
.page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.flash {
  font-size: 11.5px;
  color: var(--amber);
  border-left: 2px solid var(--amber);
  padding-left: 9px;
}
.grid {
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}
.list {
  border-right: 1px solid var(--rule-soft);
  padding-right: 14px;
  position: sticky;
  top: 58px;
}
.list__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--rule);
  margin-bottom: 4px;
}
.list__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0 6px;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--rule-soft);
  border-left: 2px solid transparent;
  padding: 7px 6px 7px 8px;
  cursor: pointer;
  color: var(--ink);
  transition: background-color 140ms var(--ease), border-color 140ms var(--ease);
}
.item:hover {
  background: color-mix(in oklch, var(--ground-3) 55%, var(--ground));
}
.item.is-on {
  border-left-color: var(--amber);
  background: color-mix(in oklch, var(--ground-3) 80%, var(--ground));
}
.item__brand {
  grid-column: 1 / -1;
  font-size: 9px;
  color: var(--ink-faint);
}
.item__name {
  font-size: 12.5px;
}
.item__stats {
  grid-column: 1 / -1;
  font-size: 9px;
  color: var(--ink-faint);
}
.item__off {
  grid-row: 2;
  font-size: 8.5px;
  color: var(--neg);
}
.editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.editor__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--rule);
}
.editor__title {
  font-size: 21px;
  font-weight: 500;
  letter-spacing: -0.01em;
}
.editor__code {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.form-grid--tight {
  grid-template-columns: 100px 100px 100px minmax(0, 1fr);
}
.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.fld--wide {
  grid-column: span 2;
}
.tags {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.tags__area {
  resize: vertical;
  font-size: 12px;
  line-height: 1.8;
}
.toggle {
  background: transparent;
  border: 1px solid var(--rule-soft);
  color: var(--ink-faint);
  font-family: var(--mono);
  font-size: 10.5px;
  padding: 7px 8px;
  cursor: pointer;
  text-align: left;
}
.toggle.is-on {
  border-color: var(--watch);
  color: var(--watch);
}
.trial {
  border: 1px solid var(--rule);
  padding: 10px 12px;
  background: color-mix(in oklch, var(--ground-2) 55%, var(--ground));
}
.trial__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
}
.trial__meta {
  font-size: 9.5px;
  color: var(--ink-faint);
}
.trial__bars {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 8px;
}
.trial__bar {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr) 52px;
  gap: 8px;
  align-items: center;
  font-size: 9.5px;
  color: var(--ink-faint);
}
.trial__track {
  height: 3px;
  background: var(--ground-4);
}
.trial__fill {
  display: block;
  height: 100%;
  background: var(--amber);
}
.trial__fill--pos {
  background: var(--watch);
}
.trial__samples {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}
.trial__sample {
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr);
  gap: 0 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--rule-soft);
}
.trial__src {
  font-size: 9px;
  color: var(--ink-faint);
}
.trial__title {
  font-size: 11.5px;
  color: var(--ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trial__hits {
  grid-column: 2;
  font-size: 9px;
  color: var(--amber-dim);
}
.trial__hits em {
  font-style: normal;
  color: var(--neg);
  margin-left: 6px;
}
.trial__note {
  font-size: 9px;
  color: var(--ink-faint);
  margin-top: 7px;
}
.dictionary {
  border-top: 1px solid var(--rule);
  padding-top: 9px;
}
.dict__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.dict__meta {
  font-size: 9px;
  color: var(--ink-faint);
}
.dict__cols {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.dict__words {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.mini {
  font-size: 9.5px;
  border: 1px solid var(--rule-soft);
  padding: 0 5px;
  color: var(--ink-soft);
}
.mini em {
  font-style: normal;
  color: var(--ink-faint);
  margin-left: 3px;
  font-size: 8.5px;
}
.mini--neg {
  border-color: color-mix(in oklch, var(--neg) 45%, var(--ground));
  color: color-mix(in oklch, var(--neg) 75%, var(--ink));
}
.mini--syn {
  border-color: var(--amber-dim);
  color: var(--amber-dim);
}
.mini--ex {
  color: var(--ink-faint);
  text-decoration: line-through;
}
@media (max-width: 1180px) {
  .form-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .form-grid--tight {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .dict__cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 900px) {
  .drafts__cols {
    grid-template-columns: minmax(0, 1fr);
  }
  .hist__row {
    grid-template-columns: 38px minmax(0, 1fr);
  }
  .hist__words {
    grid-column: 2;
  }
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .list {
    border-right: 0;
    padding-right: 0;
    position: static;
    border-bottom: 1px solid var(--rule);
    padding-bottom: 10px;
  }
  .tags {
    grid-template-columns: minmax(0, 1fr);
  }
  .fld--wide {
    grid-column: span 1;
  }
}
</style>
