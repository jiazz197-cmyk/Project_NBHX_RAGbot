<!--
  渲染聊天界面中的单条消息（用户 / AI）

  功能：
  1. 根据 role 区分用户消息和 AI 消息
  2. 用户消息：纯文本展示
  3. AI 消息：支持 Markdown 渲染（代码块、列表、表格等）
  4. AI 流式输出：支持从 &lt;think&gt;...&lt;/think&gt; 中分离“思考过程”和“最终答案”
  5. 流式阶段：当 &lt;think&gt; 尚未闭合时，实时展示思考内容；闭合后自动切换到答案流
  6. 思考区可折叠，并在答案开始后自动收起（仍可手动展开）
  7. 以暖色底区分角色（无头像，见 DESIGN.md）
  8. 可选显示消息时间

  使用场景：
  聊天页面 MessageList 中循环渲染消息列表
-->
 
<template>
  <div :class="['message-item', `message-item--${role}`]">
    <div class="message-item__content">
      <div class="message-item__bubble" :class="`message-item__bubble--${role}`">
      <template v-if="role === 'assistant'">
        <details
          v-if="hasThought"
          class="message-item__thought"
          :open="thoughtExpanded"
        >
          <summary class="message-item__thought-summary" @click.prevent="toggleThought">
            💭 思考过程
          </summary>
          <div
            v-if="isStreaming"
            :class="[
              'message-item__text',
              'message-item__stream-text',
              { 'message-item__thought-content--clamped': clampThoughtDuringAnswer },
            ]"
          >{{ parsedAssistantContent.thought }}</div>
          <div
            v-else
            :class="[
              'message-item__text',
              'message-item__markdown',
              { 'message-item__thought-content--clamped': clampThoughtDuringAnswer },
            ]"
            v-html="renderedThoughtMarkdown"
          ></div>
        </details>
        <div v-if="answerPending && !hasAnswer" class="message-item__answer-pending" aria-busy="true">
          <div class="message-item__loading-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
        <template v-else>
          <div v-if="isStreaming" class="message-item__text message-item__stream-text">{{ parsedAssistantContent.answer }}</div>
          <div v-else class="message-item__text message-item__markdown" v-html="renderedAnswerMarkdown"></div>
        </template>
      </template>
      <div v-else class="message-item__text">{{ content }}</div>
      </div>
      <div v-if="timestamp" class="message-item__time">{{ timestamp }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

interface Props {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
  isStreaming?: boolean
  /** True while the assistant reply is in flight but no answer text is visible yet */
  answerPending?: boolean
}

const props = defineProps<Props>()
const isStreaming = computed(() => Boolean(props.isStreaming))
const answerPending = computed(() => Boolean(props.answerPending))

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const wrapTableScrollContainer = (html: string): string => {
  return html
    .replace(/<table>/g, '<div class="message-item__table-scroll"><table>')
    .replace(/<\/table>/g, '</table></div>')
}

const splitAssistantContent = (content: string): { thought: string; answer: string } => {
  const text = content || ''
  const thinkStartTag = '<think>'
  const thinkEndTag = '</think>'
  const thinkStartIndex = text.indexOf(thinkStartTag)
  const thinkEndIndex = text.indexOf(thinkEndTag)

  const sanitizeThought = (value: string): string =>
    value.replace(/<\/?think>/g, '').trim()

  const sanitizeAnswer = (value: string): string =>
    value.replace(/<\/?think>/g, '').replace(/^\s+/, '')

  if (thinkStartIndex !== -1 && thinkEndIndex === -1) {
    return {
      thought: sanitizeThought(text.slice(thinkStartIndex + thinkStartTag.length)),
      answer: '',
    }
  }

  if (thinkEndIndex === -1) {
    return {
      thought: '',
      answer: sanitizeAnswer(text),
    }
  }

  const rawThought = thinkStartIndex !== -1
    ? text.slice(thinkStartIndex + thinkStartTag.length, thinkEndIndex)
    : text.slice(0, thinkEndIndex)
  const rawAnswer = text.slice(thinkEndIndex + thinkEndTag.length)

  return {
    thought: sanitizeThought(rawThought),
    answer: sanitizeAnswer(rawAnswer),
  }
}

const parsedAssistantContent = computed(() => splitAssistantContent(props.content || ''))
const hasThought = computed(() => parsedAssistantContent.value.thought.length > 0)
const hasAnswer = computed(() => parsedAssistantContent.value.answer.trim().length > 0)
const clampThoughtDuringAnswer = computed(() => Boolean(props.isStreaming && hasAnswer.value))
const renderedThoughtMarkdown = computed(() =>
  DOMPurify.sanitize(
    wrapTableScrollContainer(md.render(parsedAssistantContent.value.thought || '')),
    { USE_PROFILES: { html: true } }
  )
)
const renderedAnswerMarkdown = computed(() =>
  DOMPurify.sanitize(
    wrapTableScrollContainer(md.render(parsedAssistantContent.value.answer || '')),
    { USE_PROFILES: { html: true } }
  )
)

const thoughtExpanded = ref(true)
const hasAutoCollapsedForCurrentMessage = ref(false)

watch(
  () => props.content,
  () => {
    const nextHasThought = hasThought.value
    const nextHasAnswer = hasAnswer.value

    if (!nextHasThought) {
      thoughtExpanded.value = true
      hasAutoCollapsedForCurrentMessage.value = false
      return
    }

    if (nextHasThought && !nextHasAnswer) {
      thoughtExpanded.value = true
      hasAutoCollapsedForCurrentMessage.value = false
      return
    }

    if (nextHasThought && nextHasAnswer && !hasAutoCollapsedForCurrentMessage.value) {
      thoughtExpanded.value = false
      hasAutoCollapsedForCurrentMessage.value = true
    }
  },
  { immediate: true }
)

const toggleThought = () => {
  thoughtExpanded.value = !thoughtExpanded.value
}
</script>

<style lang="scss" scoped>
/* Colors from frontend/DESIGN.md — warm neutrals, no cool blue-grays */
$color-parchment: #f5f4ed;
$color-ivory: #faf9f5;
$color-warm-sand: #e8e6dc;
$color-near-black: #141413;
$color-charcoal-warm: #4d4c48;
$color-olive-gray: #5e5d59;
$color-stone-gray: #87867f;
$color-border-cream: #f0eee6;
$color-border-warm: #e8e6dc;
$color-terracotta: #c96442;
$color-ring: #d1cfc5;

.message-item {
  display: flex;
  width: 100%;
  padding: 10px 16px;
  box-sizing: border-box;

  &--user {
    justify-content: flex-end;

    .message-item__content {
      align-items: flex-end;
    }
  }

  &--assistant {
    justify-content: flex-start;

    .message-item__content {
      align-items: flex-start;
    }
  }

  &__content {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-width: min(100%, 720px);
    width: fit-content;
  }

  &__bubble {
    border-radius: 12px;
    padding: 12px 16px;
    line-height: 1.6;
    text-align: left;
    /* ring shadow = border-like halo (DESIGN.md depth) */
    box-shadow: 0 0 0 1px $color-border-cream;

    &--user {
      background: $color-warm-sand;
      color: $color-charcoal-warm;
      box-shadow: 0 0 0 1px $color-ring;
    }

    &--assistant {
      background: $color-ivory;
      color: $color-near-black;
      max-width: 100%;
    }
  }

  &__text {
    padding: 0;
    border-radius: 0;
    line-height: 1.6;
    word-wrap: break-word;
    overflow-x: visible;
  }

  &__time {
    font-size: 12px;
    color: $color-stone-gray;
    padding: 0 2px 0 6px;
    letter-spacing: 0.12px;
  }
}

.message-item__markdown {
  :deep(p) {
    margin: 0 0 12px;
  }

  :deep(p:last-child) {
    margin-bottom: 0;
  }

  :deep(ul),
  :deep(ol) {
    margin: 0 0 12px;
    padding-left: 20px;
  }

  :deep(pre) {
    margin: 0 0 12px;
    padding: 12px;
    border-radius: 8px;
    background: $color-parchment;
    border: 1px solid $color-border-cream;
    overflow-x: auto;
  }

  :deep(code) {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono',
      'Courier New', monospace;
  }

  :deep(table) {
    width: max-content;
    min-width: 100%;
    border-collapse: collapse;
    margin: 0 0 12px;
  }

  :deep(th),
  :deep(td) {
    border: 1px solid $color-border-warm;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
  }

  :deep(th) {
    background: $color-parchment;
    color: $color-near-black;
    font-weight: 600;
  }

  :deep(.message-item__table-scroll) {
    width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    margin: 0 0 12px;
    scrollbar-width: thin;
    scrollbar-color: $color-ring $color-parchment;
  }

  :deep(.message-item__table-scroll::-webkit-scrollbar) {
    height: 8px;
  }

  :deep(.message-item__table-scroll::-webkit-scrollbar-track) {
    background: $color-parchment;
    border-radius: 999px;
  }

  :deep(.message-item__table-scroll::-webkit-scrollbar-thumb) {
    background: $color-ring;
    border-radius: 999px;
  }

  :deep(.message-item__table-scroll::-webkit-scrollbar-thumb:hover) {
    background: $color-stone-gray;
  }
}

.message-item__thought {
  border-radius: 8px;
  background: $color-parchment;
  border: 1px solid $color-border-cream;
  border-left: 4px solid $color-terracotta;
  padding: 10px 12px;
  margin-bottom: 10px;

  &[open] {
    padding-bottom: 12px;
  }
}

.message-item__thought-summary {
  cursor: pointer;
  color: $color-olive-gray;
  font-size: 13px;
  font-weight: 600;
  user-select: none;
}

.message-item__thought-content--clamped {
  max-height: calc(1.6em * 5);
  overflow-y: auto;
  overflow-x: hidden;
}

.message-item__stream-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.message-item__answer-pending {
  min-height: 1.5em;
  display: flex;
  align-items: center;
}

.message-item__loading-dots {
  display: flex;
  gap: 4px;

  span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: $color-terracotta;
    animation: message-item-dot-bounce 1.4s infinite ease-in-out both;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }
  }
}

@keyframes message-item-dot-bounce {
  0%,
  80%,
  100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
