<template>
  <section class="diff" data-test="ruleset-diff">
    <div class="diff__head">
      <div>
        <span class="eyebrow">同一批候选 · 新旧规则对照</span>
        <div class="diff__title">
          <span class="mono">线上</span>
          <span class="diff__arrow">→</span>
          <span class="mono">草稿</span>
          <span class="diff__batch mono">{{ result.batch.total }} 条候选（窗口 {{ result.batch.trial_or_inbox }} · 库内 {{ result.batch.stored }}）</span>
        </div>
      </div>
      <span class="mono diff__hash">draft {{ result.content_hash.slice(0, 10) }}</span>
    </div>

    <div class="diff__counts">
      <span class="cnt cnt--add">新增 <b class="mono">{{ result.compare.added }}</b></span>
      <span class="cnt cnt--drop">漏掉 <b class="mono">{{ result.compare.dropped }}</b></span>
      <span class="cnt cnt--ex">被排除 <b class="mono">{{ result.compare.excluded }}</b></span>
      <span class="cnt cnt--move">换组 <b class="mono">{{ result.compare.moved }}</b></span>
      <span class="cnt">不变 <b class="mono">{{ result.compare.unchanged }}</b></span>
    </div>

    <div class="diff__cols">
      <div class="diff__col">
        <div class="diff__colhead">
          <span class="label">采集窗口候选（未入库）</span>
          <span class="mono">
            新增 {{ result.trial.compare.added }} · 漏掉 {{ result.trial.compare.dropped }} ·
            排除 {{ result.trial.compare.excluded }}
          </span>
        </div>
        <ul class="rows">
          <li
            v-for="row in result.trial.rows.filter((item: DiffRow) => item.change !== 'unchanged').slice(0, 12)"
            :key="row.external_id"
            class="row"
            :class="`row--${row.change}`"
          >
            <span class="row__change mono">{{ changeLabel(row.change) }}</span>
            <span class="row__title">{{ row.title }}</span>
            <span class="row__detail mono">
              {{ row.new.code ?? row.old.code ?? '—' }}
              <em v-if="row.new.hits.length">＋{{ row.new.hits.join(' / ') }}</em>
              <em v-if="row.new.excludes.length" class="row__ex">⊘ {{ row.new.excludes.join(' / ') }}</em>
              <em v-if="row.change === 'dropped' && row.old.hits.length" class="row__old">原 {{ row.old.hits.join(' / ') }}</em>
            </span>
          </li>
          <li v-if="!result.trial.rows.some((item: DiffRow) => item.change !== 'unchanged')" class="empty">
            采集窗口这一批候选在新旧规则下结果完全一致
          </li>
        </ul>
      </div>

      <div class="diff__col">
        <div class="diff__colhead">
          <span class="label">库内提及（归属 / 情感）</span>
          <span class="mono">
            换组 {{ result.mentions.compare.moved }} · 漏掉 {{ result.mentions.compare.dropped }} ·
            排除 {{ result.mentions.compare.excluded }}
          </span>
        </div>
        <ul class="rows">
          <li
            v-for="row in result.mentions.rows.filter((item: DiffRow) => item.change !== 'unchanged').slice(0, 12)"
            :key="row.external_id"
            class="row"
            :class="`row--${row.change}`"
          >
            <span class="row__change mono">{{ changeLabel(row.change) }}</span>
            <span class="row__title">{{ row.title }}</span>
            <span class="row__detail mono">
              {{ row.old.code ?? '—' }} → {{ row.new.code ?? '—' }}
              <em :class="row.sentiment.label === 'neg' ? 'row__neg' : ''">
                {{ sentimentLabel(row.sentiment.label) }} {{ row.sentiment.score.toFixed(2) }}
              </em>
              <em v-if="row.sentiment.hits.length">证据 {{ row.sentiment.hits.join(' / ') }}</em>
            </span>
          </li>
          <li v-if="!result.mentions.rows.some((item: DiffRow) => item.change !== 'unchanged')" class="empty">
            库内提及的归属与情感在新旧规则下一致
          </li>
        </ul>
      </div>
    </div>

    <div class="diff__cols">
      <div class="diff__col">
        <div class="diff__colhead">
          <span class="label">情感分布（被召回候选）</span>
          <span class="mono">旧 → 新</span>
        </div>
        <div class="bars">
          <div v-for="label in (['pos', 'neu', 'neg'] as const)" :key="label" class="bar">
            <span class="bar__label">{{ sentimentLabel(label) }}</span>
            <span class="bar__pair mono">
              {{ result.sentiment.old[label] ?? 0 }} → {{ result.sentiment.new[label] ?? 0 }}
            </span>
          </div>
        </div>
        <p class="diff__note">{{ result.sentiment.note }}</p>
      </div>

      <div class="diff__col">
        <div class="diff__colhead">
          <span class="label">爆发窗口差异</span>
          <span class="mono">旧 {{ result.burst.old.events.length }} · 新 {{ result.burst.new.events.length }} 个</span>
        </div>
        <ul class="rows">
          <li
            v-for="(row, index) in result.burst.rows.filter((item) => item.change !== 'unchanged').slice(0, 8)"
            :key="index"
            class="row"
            :class="`row--${row.change}`"
          >
            <span class="row__change mono">{{ changeLabel(row.change) }}</span>
            <span class="row__title">{{ (row.new ?? row.old)?.name }}</span>
            <span class="row__detail mono">
              {{ windowLabel(row.new ?? row.old) }}
              z={{ (row.new ?? row.old)?.z_score }}
              · {{ levelLabel((row.new ?? row.old)?.level) }}
              <em v-if="row.old && row.new">旧 z={{ row.old.z_score }} → 新 z={{ row.new.z_score }}</em>
            </span>
          </li>
          <li v-if="!result.burst.rows.some((item) => item.change !== 'unchanged')" class="empty">
            爆发事件在这批候选上没有变化
          </li>
        </ul>
      </div>
    </div>

    <div class="diff__rules">
      <div class="diff__colhead">
        <span class="label">规则差异</span>
        <span class="mono">仅列出被改动的组</span>
      </div>
      <ul class="rulelist">
        <li v-for="item in changedRules" :key="item.new.group_id" class="rule">
          <span class="mono rule__code">{{ item.new.code }}</span>
          <span class="rule__line">
            <em>主词</em> {{ item.old.keywords.join(' / ') || '—' }} → {{ item.new.keywords.join(' / ') || '—' }}
          </span>
          <span class="rule__line">
            <em>同义词</em> {{ item.old.synonyms.join(' / ') || '—' }} → {{ item.new.synonyms.join(' / ') || '—' }}
          </span>
          <span class="rule__line">
            <em>排除词</em> {{ item.old.excludes.join(' / ') || '—' }} → {{ item.new.excludes.join(' / ') || '—' }}
          </span>
          <span class="rule__line mono rule__num">
            z {{ item.old.z_threshold }} → {{ item.new.z_threshold }} · 最小量 {{ item.old.min_count }} → {{ item.new.min_count }}
            · {{ item.old.enabled ? '启用' : '停用' }} → {{ item.new.enabled ? '启用' : '停用' }}
          </span>
        </li>
        <li v-if="!changedRules.length" class="empty">规则内容与线上一致（该草稿没有改动）</li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { BurstLevel, DiffResult, DiffRow, RuleRow } from '../../types/domain'

const props = defineProps<{ result: DiffResult }>()

const CHANGE_LABELS: Record<string, string> = {
  added: '新增',
  dropped: '漏掉',
  excluded: '被排除',
  moved: '换组',
  changed: '变化',
  unchanged: '不变',
}

const changedRules = computed<{ old: RuleRow; new: RuleRow }[]>(() => {
  const olds = new Map<string, RuleRow>(props.result.rules.old.map((item) => [item.group_id, item]))
  return props.result.rules.new
    .filter((item) => olds.get(item.group_id)?.hash !== item.hash)
    .map((item) => ({ old: olds.get(item.group_id) as RuleRow, new: item }))
})

function changeLabel(change: string): string {
  return CHANGE_LABELS[change] ?? change
}

function sentimentLabel(label: string): string {
  return { pos: '正面', neu: '中性', neg: '负面' }[label] ?? label
}

function levelLabel(level?: BurstLevel): string {
  const labels: Record<string, string> = { watch: '关注', alert: '预警', critical: '危急' }
  return level ? labels[level] ?? level : ''
}

function windowLabel(event: { window_start?: string } | null | undefined): string {
  if (!event?.window_start) return '—'
  const date = new Date(event.window_start)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${month}-${day} ${String(date.getHours()).padStart(2, '0')}:00`
}
</script>

<style scoped>
.diff {
  border: 1px solid var(--rule);
  padding: 11px 13px;
  background: color-mix(in oklch, var(--ground-2) 55%, var(--ground));
  display: flex;
  flex-direction: column;
  gap: 11px;
}
.diff__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  border-bottom: 1px solid var(--rule-soft);
  padding-bottom: 7px;
}
.diff__title {
  display: flex;
  align-items: baseline;
  gap: 9px;
  font-size: 10px;
  color: var(--ink-soft);
  margin-top: 3px;
}
.diff__arrow {
  color: var(--amber);
}
.diff__batch {
  color: var(--ink-faint);
  font-size: 9.5px;
}
.diff__hash {
  font-size: 9px;
  color: var(--ink-faint);
}
.diff__counts {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.cnt {
  font-size: 9.5px;
  color: var(--ink-faint);
  border: 1px solid var(--rule-soft);
  padding: 2px 7px;
}
.cnt b {
  font-size: 11px;
  margin-left: 3px;
}
.cnt--add b {
  color: var(--pos);
}
.cnt--drop b {
  color: var(--neg);
}
.cnt--ex b {
  color: var(--amber);
}
.cnt--move b {
  color: var(--watch);
}
.diff__cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.diff__colhead {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--rule-soft);
}
.diff__colhead .mono {
  font-size: 9px;
  color: var(--ink-faint);
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.row {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 0 7px;
  padding: 4px 0;
  border-bottom: 1px solid var(--rule-soft);
}
.row__change {
  font-size: 9px;
  color: var(--ink-faint);
}
.row--added .row__change {
  color: var(--pos);
}
.row--dropped .row__change {
  color: var(--neg);
}
.row--excluded .row__change {
  color: var(--amber);
}
.row--moved .row__change,
.row--changed .row__change {
  color: var(--watch);
}
.row__title {
  font-size: 11px;
  color: var(--ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row__detail {
  grid-column: 2;
  font-size: 9px;
  color: var(--ink-faint);
  display: flex;
  flex-wrap: wrap;
  gap: 0 8px;
}
.row__detail em {
  font-style: normal;
}
.row__ex,
.row__old {
  color: var(--neg);
}
.row__neg {
  color: var(--neg);
}
.empty {
  font-size: 10px;
  color: var(--ink-faint);
  padding: 5px 0;
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}
.bar {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 8px;
  font-size: 10px;
  color: var(--ink-soft);
}
.diff__note {
  font-size: 9px;
  color: var(--ink-faint);
  margin: 7px 0 0;
}
.diff__rules {
  border-top: 1px solid var(--rule-soft);
  padding-top: 7px;
}
.rulelist {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.rule {
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  gap: 1px 9px;
  font-size: 9.5px;
  color: var(--ink-faint);
}
.rule__code {
  grid-row: 1 / span 5;
  color: var(--amber-dim);
  font-size: 9px;
}
.rule__line em {
  font-style: normal;
  color: var(--rule-bright);
  margin-right: 4px;
}
.rule__num {
  font-size: 9px;
}
@media (max-width: 1180px) {
  .diff__cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
