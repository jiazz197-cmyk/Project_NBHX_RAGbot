<template>
  <div class="page">
    <div class="page-header">
      <div class="page-header__left">
        <h1 class="page-header__title">营业订单信息</h1>
        <div class="page__tabs">
      <button
        class="page__tab"
        :class="{ 'page__tab--active': activeTab === 'form' }"
        type="button"
        @click="activeTab = 'form'"
      >
        填写表单
        </button>
        <button
          class="page__tab"
          :class="{ 'page__tab--active': activeTab === 'records' }"
          type="button"
          @click="switchToRecords"
        >
          {{ isAdmin ? '全部表单' : '我的表单' }}
        </button>
        <button
          v-if="revisionCount > 0 || activeTab === 'revision'"
          class="page__tab"
          :class="{ 'page__tab--active': activeTab === 'revision', 'page__tab--badge': revisionCount > 0 && activeTab !== 'revision' }"
          type="button"
          @click="switchToRevision"
        >
          待修改<template v-if="revisionCount > 0"> ({{ revisionCount }})</template>
        </button>
      </div>
    </div>
  </div>

    <div class="page__content">

      <!-- 填写表单 -->
      <form v-if="activeTab === 'form'" class="form" @submit.prevent="submitForm">

        <div v-if="revisingFormId" class="form-revision-banner">
          <span>正在修改被退回的报单，请编辑后点击「重新提交」。规格书与原价书均需重新上传。</span>
          <button class="form-revision-banner__cancel" type="button" @click="cancelRevise">取消修改</button>
        </div>

        <section class="form-section">
          <div class="form-section__title">基本信息</div>
          <div class="form-grid">
            <div class="form-field">
              <label class="form-field__label" for="closing_date">成交时间</label>
              <input
                id="closing_date"
                v-model="form.closing_date"
                type="date"
                class="form-field__input"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="customer_name">客户名称</label>
              <input
                id="customer_name"
                v-model="form.customer_name"
                type="text"
                class="form-field__input"
                placeholder="请输入客户名称"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="product_type">产品类型</label>
              <input
                id="product_type"
                v-model="form.product_type"
                type="text"
                class="form-field__input"
                placeholder="请输入产品类型"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="model_spec">型号规格</label>
              <input
                id="model_spec"
                v-model="form.model_spec"
                type="text"
                class="form-field__input"
                placeholder="请输入型号规格"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="quantity">数量</label>
              <input
                id="quantity"
                v-model.number="form.quantity"
                type="number"
                min="0"
                class="form-field__input"
                placeholder="请输入数量"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="price_excluding_tax">不含税单价</label>
              <input
                id="price_excluding_tax"
                v-model.number="form.price_excluding_tax"
                type="number"
                min="0"
                step="0.01"
                class="form-field__input"
                placeholder="请输入不含税单价"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="production_number">制造编号</label>
              <input
                id="production_number"
                v-model="form.production_number"
                type="text"
                class="form-field__input"
                placeholder="请输入制造编号"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="contract_number">合同编号</label>
              <input
                id="contract_number"
                v-model="form.contract_number"
                type="text"
                class="form-field__input"
                placeholder="请输入合同编号"
              />
            </div>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section__title">物料与称重</div>
          <div class="form-grid">
            <div class="form-field">
              <label class="form-field__label" for="material_name">物料名称</label>
              <input
                id="material_name"
                v-model="form.material_name"
                type="text"
                class="form-field__input"
                placeholder="请输入物料名称"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="weighing_spec">称重规格</label>
              <input
                id="weighing_spec"
                v-model="form.weighing_spec"
                type="text"
                class="form-field__input"
                placeholder="例如：10g"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="speed">速度（次/分）</label>
              <input
                id="speed"
                v-model.number="form.speed"
                type="number"
                min="0"
                class="form-field__input"
                placeholder="请输入速度"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="precision">精度</label>
              <input
                id="precision"
                v-model="form.precision"
                type="text"
                class="form-field__input"
                placeholder="例如：≤1g"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="packaging_machine_type">包装机类型</label>
              <input
                id="packaging_machine_type"
                v-model="form.packaging_machine_type"
                type="text"
                class="form-field__input"
                placeholder="请输入包装机类型"
              />
            </div>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section__title">零部件配置</div>
          <div class="form-grid">
            <div class="form-field">
              <label class="form-field__label" for="top_cone_type">顶锥形式</label>
              <input
                id="top_cone_type"
                v-model="form.top_cone_type"
                type="text"
                class="form-field__input"
                placeholder="请输入顶锥形式"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="linear_vibration_type">线振形式</label>
              <input
                id="linear_vibration_type"
                v-model="form.linear_vibration_type"
                type="text"
                class="form-field__input"
                placeholder="请输入线振形式"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="material_layer_ring">料层圈</label>
              <input
                id="material_layer_ring"
                v-model="form.material_layer_ring"
                type="text"
                class="form-field__input"
                placeholder="请输入料层圈"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="feed_hopper">供料斗</label>
              <input
                id="feed_hopper"
                v-model="form.feed_hopper"
                type="text"
                class="form-field__input"
                placeholder="请输入供料斗"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="metering_hopper">计量斗</label>
              <input
                id="metering_hopper"
                v-model="form.metering_hopper"
                type="text"
                class="form-field__input"
                placeholder="请输入计量斗"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="memory_hopper">记忆斗</label>
              <input
                id="memory_hopper"
                v-model="form.memory_hopper"
                type="text"
                class="form-field__input"
                placeholder="请输入记忆斗"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="chute_angle">溜槽角度</label>
              <input
                id="chute_angle"
                v-model="form.chute_angle"
                type="text"
                class="form-field__input"
                placeholder="例如：50°"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="collection_hopper_type">集合斗形式</label>
              <input
                id="collection_hopper_type"
                v-model="form.collection_hopper_type"
                type="text"
                class="form-field__input"
                placeholder="请输入集合斗形式"
              />
            </div>
            <div class="form-field">
              <label class="form-field__label" for="scale_type">秤体类型</label>
              <input
                id="scale_type"
                v-model="form.scale_type"
                type="text"
                class="form-field__input"
                placeholder="请输入秤体类型"
              />
            </div>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section__title">图片附件</div>
          <p class="form-section__hint">请上传「规格书」和「原价书」各一张，两项均为必填。</p>
          <div class="image-upload-area">
            <div
              v-for="(label, index) in IMAGE_LABELS"
              :key="label"
              class="image-upload-slot"
            >
              <div class="image-upload-slot__label">
                {{ label }}<span class="image-upload-slot__required">*</span>
              </div>
              <div class="image-upload-item">
                <input
                  :ref="(el) => setImageInputRef(el as HTMLInputElement | null, index)"
                  type="file"
                  accept="image/*"
                  style="display:none"
                  @change="onImageSelected($event, index)"
                />
                <div
                  v-if="!hasImageAt(index)"
                  class="image-upload-placeholder"
                  @click="triggerImageInput(index)"
                >
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                  <span>上传{{ label }}</span>
                </div>
                <div v-else class="image-preview">
                  <img :src="getPreviewAt(index)!" :alt="label" />
                  <button type="button" class="image-remove-btn" @click="removeImage(index)">&times;</button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div class="form-actions">
          <button class="form-actions__reset" type="button" @click="resetForm">重置</button>
          <button class="form-actions__submit" type="submit" :disabled="submitting">
            {{ submitting ? (revisingFormId ? '重新提交中…' : '提交中…') : (revisingFormId ? '重新提交' : '提交') }}
          </button>
        </div>

      </form>

      <!-- 表单记录 -->
      <div v-else-if="activeTab === 'records'" class="records">

        <div class="records__toolbar">
          <button class="records__refresh" type="button" :disabled="loadingRecords" @click="loadRecords">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              :class="{ 'records__refresh-icon--spinning': loadingRecords }"
              class="records__refresh-icon"
              aria-hidden="true"
            >
              <path
                d="M1 4v6h6M23 20v-6h-6"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            刷新
          </button>
          <span class="records__count">共 {{ records.length }} 条</span>
        </div>

        <div v-if="loadingRecords" class="records__loading">
          <div class="records__loading-dots">
            <span></span><span></span><span></span>
          </div>
        </div>

        <div v-else-if="records.length === 0" class="records__empty">
          暂无提交记录
        </div>

        <div v-else class="records__list">
          <div
            v-for="record in records"
            :key="record.id"
            class="record-card"
            :class="{
              'record-card--expanded': expandedId === record.id,
              'record-card--pending': record.status === 'pending',
              'record-card--approved': record.status === 'approved',
              'record-card--rejected': record.status === 'rejected',
              'record-card--pending-revision': record.status === 'pending_revision',
            }"
          >
            <div class="record-card__header" @click="toggleExpand(record.id)">
              <div class="record-card__meta">
                <span class="record-card__time">{{ record.upload_time || '—' }}</span>
                <span v-if="isAdmin && record.uploader" class="record-card__uploader">
                  {{ record.uploader }}
                </span>
                <span class="record-card__summary">{{ getSummary(record.text) }}</span>
              </div>
              <div class="record-card__right">
                <span class="status-badge" :class="`status-badge--${record.status}`">
                  <span class="status-badge__dot"></span>
                  {{ getStatusLabel(record.status) }}
                </span>
                <div v-if="isAdmin && record.status === 'approved'" class="action-buttons">
                  <button
                    class="approve-btn reject-btn"
                    :disabled="deletingApprovedId === record.id"
                    @click.stop="openDeleteApprovedDialog(record.id)"
                  >
                    {{ deletingApprovedId === record.id ? '...' : '删除' }}
                  </button>
                </div>
                <div v-if="isAdmin && (record.status === 'rejected' || record.status === 'pending_revision')" class="action-buttons">
                  <button
                    class="approve-btn reject-btn"
                    :disabled="deletingRejectedId === record.id"
                    @click.stop="deleteRejectedRecord(record.id)"
                  >
                    {{ deletingRejectedId === record.id ? '...' : '删除' }}
                  </button>
                </div>
                <div v-if="canReviseRecord(record)" class="action-buttons">
                  <button
                    class="approve-btn revise-btn"
                    :disabled="revisingId === record.id"
                    @click.stop="startRevise(record)"
                  >
                    {{ revisingId === record.id ? '...' : '修改' }}
                  </button>
                </div>
                <div v-if="isAdmin && record.status === 'pending'" class="action-buttons">
                  <button
                    class="approve-btn reject-btn"
                    :disabled="approvingId === record.id"
                    @click.stop="rejectRecord(record.id)"
                  >
                    {{ approvingId === record.id ? '...' : '不通过' }}
                  </button>
                  <button
                    class="approve-btn"
                    :disabled="approvingId === record.id"
                    @click.stop="approveRecord(record.id)"
                  >
                    {{ approvingId === record.id ? '...' : '通过' }}
                  </button>
                </div>
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  class="record-card__chevron"
                  aria-hidden="true"
                >
                  <path
                    d="M6 9l6 6 6-6"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </div>
            </div>

            <div v-if="expandedId === record.id" class="record-card__body">
              <div class="record-fields">
                <div
                  v-for="field in parseFields(record.text)"
                  :key="field.label"
                  class="record-field"
                >
                  <span class="record-field__label">{{ field.label }}</span>
                  <span class="record-field__value">{{ field.value }}</span>
                </div>
              </div>
              <div v-if="record.image_url_1 || record.image_url_2" class="record-images">
                <a v-if="record.image_url_1"
                   :href="getImageDownloadUrl(record.image_url_1)"
                   target="_blank" class="record-image-link">
                  规格书
                </a>
                <a v-if="record.image_url_2"
                   :href="getImageDownloadUrl(record.image_url_2)"
                   target="_blank" class="record-image-link">
                  原价书
                </a>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- 待修改 -->
      <div v-else-if="activeTab === 'revision'" class="records">

        <div class="records__toolbar">
          <button class="records__refresh" type="button" :disabled="loadingRevision" @click="loadRevisionRecords">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              :class="{ 'records__refresh-icon--spinning': loadingRevision }"
              class="records__refresh-icon"
              aria-hidden="true"
            >
              <path
                d="M1 4v6h6M23 20v-6h-6"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            刷新
          </button>
          <span class="records__count">共 {{ revisionRecords.length }} 条</span>
        </div>

        <div v-if="loadingRevision" class="records__loading">
          <div class="records__loading-dots">
            <span></span><span></span><span></span>
          </div>
        </div>

        <div v-else-if="revisionRecords.length === 0" class="records__empty">
          暂无待修改的表单
        </div>

        <div v-else class="records__list">
          <div
            v-for="record in revisionRecords"
            :key="record.id"
            class="record-card record-card--pending-revision"
            :class="{
              'record-card--expanded': expandedRevisionId === record.id,
            }"
          >
            <div class="record-card__header" @click="toggleExpandRevision(record.id)">
              <div class="record-card__meta">
                <span class="record-card__time">{{ record.upload_time || '—' }}</span>
                <span v-if="isAdmin && record.uploader" class="record-card__uploader">
                  {{ record.uploader }}
                </span>
                <span class="record-card__summary">{{ getSummary(record.text) }}</span>
              </div>
              <div class="record-card__right">
                <span class="status-badge status-badge--pending-revision">
                  <span class="status-badge__dot"></span>
                  待修改
                </span>
                <div v-if="isAdmin" class="action-buttons">
                  <button
                    class="approve-btn reject-btn"
                    :disabled="deletingRejectedId === record.id"
                    @click.stop="deleteRejectedRecord(record.id)"
                  >
                    {{ deletingRejectedId === record.id ? '...' : '删除' }}
                  </button>
                </div>
                <div v-if="canReviseRecord(record)" class="action-buttons">
                  <button
                    class="approve-btn revise-btn"
                    :disabled="revisingId === record.id"
                    @click.stop="startRevise(record)"
                  >
                    {{ revisingId === record.id ? '...' : '修改' }}
                  </button>
                </div>
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  class="record-card__chevron"
                  aria-hidden="true"
                >
                  <path
                    d="M6 9l6 6 6-6"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </div>
            </div>

            <div v-if="expandedRevisionId === record.id" class="record-card__body">
              <div class="record-fields">
                <div
                  v-for="field in parseFields(record.text)"
                  :key="field.label"
                  class="record-field"
                >
                  <span class="record-field__label">{{ field.label }}</span>
                  <span class="record-field__value">{{ field.value }}</span>
                </div>
              </div>
              <div v-if="record.image_url_1 || record.image_url_2" class="record-images">
                <a v-if="record.image_url_1"
                   :href="getImageDownloadUrl(record.image_url_1)"
                   target="_blank" class="record-image-link">
                  规格书
                </a>
                <a v-if="record.image_url_2"
                   :href="getImageDownloadUrl(record.image_url_2)"
                   target="_blank" class="record-image-link">
                  原价书
                </a>
              </div>
            </div>
          </div>
        </div>

      </div>

    </div>

    <ConfirmDialog
      v-model="showDeleteApprovedDialog"
      title="删除已通过表单"
      message="确定要删除这条已通过表单吗？此操作无法撤销。"
      type="danger"
      confirm-text="删除"
      cancel-text="取消"
      @confirm="confirmDeleteApproved"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ConfirmDialog, useToast } from '@yamato/components'
import {
  approveClosingForm,
  deleteApprovedClosingForm,
  deleteClosingFormImage,
  deleteRejectedClosingForm,
  listClosingFormRecords,
  rejectClosingForm,
  reviseClosingForm,
  submitClosingForm,
  uploadClosingFormImage,
} from '../services/closing_form'
import { readUsername, readUserRole } from '../services/auth'
import { getAuthTokenFromStorage } from '../services/token_storage'
import { config } from '../config'

interface FormData {
  closing_date: string
  customer_name: string
  product_type: string
  model_spec: string
  quantity: number | null
  price_excluding_tax: number | null
  production_number: string
  contract_number: string
  material_name: string
  weighing_spec: string
  speed: number | null
  precision: string
  packaging_machine_type: string
  top_cone_type: string
  linear_vibration_type: string
  material_layer_ring: string
  feed_hopper: string
  metering_hopper: string
  memory_hopper: string
  chute_angle: string
  collection_hopper_type: string
  scale_type: string
}

interface FormRecord {
  id: string
  text: string
  upload_time: string | null
  uploader: string
  status: string
  image_url_1?: string | null
  image_url_2?: string | null
}

interface ParsedField {
  label: string
  value: string
}

const IMAGE_LABELS = ['规格书', '原价书'] as const

const FIELD_LABEL_MAP: Record<string, keyof FormData> = {
  '日期': 'closing_date',
  '成交时间': 'closing_date',
  '客户名称': 'customer_name',
  '产品类型': 'product_type',
  '型号规格': 'model_spec',
  '数量': 'quantity',
  '原价不含税': 'price_excluding_tax',
  '生产制造编号': 'production_number',
  '合同编号': 'contract_number',
  '物料名称': 'material_name',
  '称重规格': 'weighing_spec',
  '速度': 'speed',
  '精度': 'precision',
  '包装机类型': 'packaging_machine_type',
  '顶锥形式': 'top_cone_type',
  '线振形式': 'linear_vibration_type',
  '料层调整圈': 'material_layer_ring',
  '供料斗': 'feed_hopper',
  '计量斗': 'metering_hopper',
  '记忆斗': 'memory_hopper',
  '溜槽角度': 'chute_angle',
  '集合斗形式': 'collection_hopper_type',
  '单双秤/混料/外挂/特殊': 'scale_type',
}

const { showSuccess, showError } = useToast()
const route = useRoute()

const activeTab = ref<'form' | 'records' | 'revision'>('form')
const currentUsername = ref(readUsername())

const isAdmin = computed(() => {
  const role = readUserRole()
  return role === 'admin' || role === 'superuser'
})

const isRevisionStatus = (status: string): boolean =>
  status === 'pending_revision' || status === 'rejected'

const canReviseRecord = (record: FormRecord): boolean =>
  isRevisionStatus(record.status) && record.uploader === currentUsername.value

const createEmptyForm = (): FormData => ({
  closing_date: '',
  customer_name: '',
  product_type: '',
  model_spec: '',
  quantity: null,
  price_excluding_tax: null,
  production_number: '',
  contract_number: '',
  material_name: '',
  weighing_spec: '',
  speed: null,
  precision: '',
  packaging_machine_type: '',
  top_cone_type: '',
  linear_vibration_type: '',
  material_layer_ring: '',
  feed_hopper: '',
  metering_hopper: '',
  memory_hopper: '',
  chute_angle: '',
  collection_hopper_type: '',
  scale_type: '',
})

const form = ref<FormData>(createEmptyForm())
const submitting = ref(false)
const revisingFormId = ref<string | null>(null)
const revisingSourceRecord = ref<FormRecord | null>(null)

const records = ref<FormRecord[]>([])
const loadingRecords = ref(false)
const expandedId = ref<string | null>(null)
const approvingId = ref<string | null>(null)
const deletingApprovedId = ref<string | null>(null)
const deletingRejectedId = ref<string | null>(null)
const showDeleteApprovedDialog = ref(false)
const approvedToDelete = ref<string | null>(null)

const revisionRecords = ref<FormRecord[]>([])
const loadingRevision = ref(false)
const expandedRevisionId = ref<string | null>(null)
const revisingId = ref<string | null>(null)

const revisionCount = computed(() => revisionRecords.value.length)

const imageFiles = ref<(File | null)[]>([null, null])
const imagePreviews = ref<(string | null)[]>([null, null])
const existingImageUrls = ref<(string | null)[]>([null, null])
const imageInputs = ref<(HTMLInputElement | null)[]>([null, null])

const setImageInputRef = (el: HTMLInputElement | null, index: number) => {
  imageInputs.value[index] = el
}

const getImageDownloadUrl = (objectName: string): string => {
  return `${config.apiBaseUrl}/closing-form/image/${encodeURIComponent(objectName)}`
}

const hasImageAt = (index: number): boolean =>
  Boolean(imageFiles.value[index] || existingImageUrls.value[index])

const getPreviewAt = (index: number): string | null =>
  imagePreviews.value[index] ?? (
    existingImageUrls.value[index]
      ? getImageDownloadUrl(existingImageUrls.value[index]!)
      : null
  )

const getMissingImageLabels = (): string[] =>
  IMAGE_LABELS.filter((_, index) => !hasImageAt(index))

const resolveFinalImageUrls = (uploadedUrls: (string | null)[]): (string | null)[] => [
  uploadedUrls[0] ?? existingImageUrls.value[0] ?? null,
  uploadedUrls[1] ?? existingImageUrls.value[1] ?? null,
]

const triggerImageInput = (index: number) => {
  imageInputs.value[index]?.click()
}

const onImageSelected = (event: Event, index: number) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  imageFiles.value[index] = file
  existingImageUrls.value[index] = null
  const reader = new FileReader()
  reader.onload = (e) => {
    imagePreviews.value[index] = e.target?.result as string
  }
  reader.readAsDataURL(file)
  input.value = ''
}

const removeImage = (index: number) => {
  imageFiles.value[index] = null
  imagePreviews.value[index] = null
  existingImageUrls.value[index] = null
  if (imageInputs.value[index]) imageInputs.value[index]!.value = ''
}

const resetForm = () => {
  form.value = createEmptyForm()
  imageFiles.value = [null, null]
  imagePreviews.value = [null, null]
  existingImageUrls.value = [null, null]
  imageInputs.value.forEach((input) => {
    if (input) input.value = ''
  })
  revisingFormId.value = null
  revisingSourceRecord.value = null
}

const cancelRevise = () => {
  resetForm()
  activeTab.value = 'revision'
}

const isTokenExpired = (): boolean => {
  const token = getAuthTokenFromStorage()
  if (!token) return true
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp * 1000 < Date.now()
  } catch { return true }
}

const uploadImagesForSubmit = async (): Promise<(string | null)[]> => {
  const urls: (string | null)[] = [null, null]
  for (let i = 0; i < 2; i++) {
    const file = imageFiles.value[i]
    if (!file) continue
    const result = await uploadClosingFormImage(file)
    urls[i] = result.object_name
  }
  return urls
}

const submitForm = async () => {
  if (submitting.value) return

  if (isTokenExpired()) {
    showError('登录已过期，请刷新页面后重新登录再提交（当前填写内容不会丢失）')
    return
  }

  const missingLabels = getMissingImageLabels()
  if (missingLabels.length > 0) {
    showError(`请上传${missingLabels.join('和')}后再提交`)
    return
  }

  submitting.value = true
  let uploadedUrls: (string | null)[] = []

  try {
    uploadedUrls = await uploadImagesForSubmit()

    const payload: Record<string, unknown> = { ...form.value }
    if (revisingFormId.value) {
      const [url1, url2] = resolveFinalImageUrls(uploadedUrls)
      payload.image_url_1 = url1
      payload.image_url_2 = url2
      await reviseClosingForm(revisingFormId.value, payload)
      showSuccess('修改已提交，等待审批')
      resetForm()
      void loadRevisionRecords()
      void loadRecords()
      activeTab.value = 'revision'
    } else {
      payload.image_url_1 = uploadedUrls[0]
      payload.image_url_2 = uploadedUrls[1]
      await submitClosingForm(payload)
      showSuccess('提交成功，等待审批')
      resetForm()
    }
  } catch (err: any) {
    if (revisingFormId.value && (err?.status === 404 || err?.status === 400)) {
      showError(
        err?.status === 404
          ? '该任务已被删除'
          : (err?.message || '该表单状态已变更，无法修改')
      )
      resetForm()
      void loadRevisionRecords()
      void loadRecords()
      activeTab.value = 'revision'
    } else {
      showError(err?.message || (revisingFormId.value ? '修改失败，请稍后重试' : '提交失败，请稍后重试'))
    }
    for (const url of uploadedUrls) {
      if (url) {
        try { await deleteClosingFormImage(url) } catch { /* best-effort */ }
      }
    }
  } finally {
    submitting.value = false
  }
}

const loadRecords = async () => {
  loadingRecords.value = true
  try {
    records.value = await listClosingFormRecords()
  } catch (err: any) {
    showError(err?.message || '加载失败')
  } finally {
    loadingRecords.value = false
  }
}

const loadRevisionRecords = async () => {
  loadingRevision.value = true
  try {
    const allRecords = await listClosingFormRecords()
    revisionRecords.value = allRecords.filter((r) => isRevisionStatus(r.status))
  } catch (err: any) {
    showError(err?.message || '加载失败')
  } finally {
    loadingRevision.value = false
  }
}

const getStatusLabel = (status: string): string => {
  switch (status) {
    case 'approved': return '已通过'
    case 'rejected': return '不通过'
    case 'pending_revision': return '待修改'
    default: return '待审批'
  }
}

const approveRecord = async (formId: string) => {
  approvingId.value = formId
  try {
    await approveClosingForm(formId)
    if (expandedId.value === formId) {
      expandedId.value = null
    }
    await loadRecords()
    showSuccess('已审批通过')
  } catch (err: any) {
    showError(err?.message || '审批失败')
  } finally {
    approvingId.value = null
  }
}

const rejectRecord = async (formId: string) => {
  approvingId.value = formId
  try {
    await rejectClosingForm(formId)
    const idx = records.value.findIndex((r) => r.id === formId)
    if (idx !== -1) records.value[idx] = { ...records.value[idx], status: 'pending_revision' }
    void loadRevisionRecords()
    showSuccess('已审批不通过，已退回待修改')
  } catch (err: any) {
    showError(err?.message || '操作失败')
  } finally {
    approvingId.value = null
  }
}

const openDeleteApprovedDialog = (formId: string) => {
  approvedToDelete.value = formId
  showDeleteApprovedDialog.value = true
}

const deleteRejectedRecord = async (formId: string) => {
  deletingRejectedId.value = formId
  try {
    await deleteRejectedClosingForm(formId)
    records.value = records.value.filter((record) => record.id !== formId)
    revisionRecords.value = revisionRecords.value.filter((record) => record.id !== formId)
    if (expandedId.value === formId) {
      expandedId.value = null
    }
    if (expandedRevisionId.value === formId) {
      expandedRevisionId.value = null
    }
    if (revisingFormId.value === formId) {
      resetForm()
      if (activeTab.value === 'form') {
        showSuccess('该表单已被删除，修改已取消')
      } else {
        showSuccess('已删除')
      }
    } else {
      showSuccess('已删除')
    }
  } catch (err: any) {
    showError(err?.message || '删除失败')
  } finally {
    deletingRejectedId.value = null
  }
}

const confirmDeleteApproved = async () => {
  if (!approvedToDelete.value) {
    return
  }

  const formId = approvedToDelete.value
  deletingApprovedId.value = formId
  try {
    await deleteApprovedClosingForm(formId)
    records.value = records.value.filter((record) => record.id !== formId)
    if (expandedId.value === formId) {
      expandedId.value = null
    }
    showSuccess('已删除已通过表单')
  } catch (err: any) {
    showError(err?.message || '删除失败')
  } finally {
    deletingApprovedId.value = null
    approvedToDelete.value = null
    showDeleteApprovedDialog.value = false
  }
}

const startRevise = (record: FormRecord) => {
  revisingId.value = record.id
  try {
    const fields = parseFields(record.text)
    const newForm = createEmptyForm()
    for (const field of fields) {
      const formKey = FIELD_LABEL_MAP[field.label]
      if (formKey) {
        const numKeys: (keyof FormData)[] = ['quantity', 'speed', 'price_excluding_tax']
        if (numKeys.includes(formKey)) {
          (newForm as any)[formKey] = field.value === '' ? null : Number(field.value)
        } else {
          (newForm as any)[formKey] = field.value
        }
      }
    }
    form.value = newForm
    revisingFormId.value = record.id
    revisingSourceRecord.value = record
    imageFiles.value = [null, null]
    imagePreviews.value = [null, null]
    existingImageUrls.value = [record.image_url_1 ?? null, record.image_url_2 ?? null]
    imageInputs.value.forEach((input) => {
      if (input) input.value = ''
    })
    activeTab.value = 'form'
  } finally {
    revisingId.value = null
  }
}

const switchToRecords = () => {
  activeTab.value = 'records'
  void loadRecords()
}

const switchToRevision = () => {
  activeTab.value = 'revision'
  void loadRevisionRecords()
}

const toggleExpand = (id: string) => {
  expandedId.value = expandedId.value === id ? null : id
}

const toggleExpandRevision = (id: string) => {
  expandedRevisionId.value = expandedRevisionId.value === id ? null : id
}

const parseFields = (text: string): ParsedField[] => {
  return text
    .split(', ')
    .map((part) => {
      const colonIdx = part.indexOf('：')
      if (colonIdx === -1) return null
      return { label: part.slice(0, colonIdx).trim(), value: part.slice(colonIdx + 1).trim() }
    })
    .filter((f): f is ParsedField => f !== null && f.label !== '图片url')
}

const getSummary = (text: string): string => {
  const fields = parseFields(text)
  const customer = fields.find((f) => f.label === '客户名称')?.value ?? ''
  const model = fields.find((f) => f.label === '型号规格')?.value ?? ''
  const qty = fields.find((f) => f.label === '数量')?.value ?? ''
  const parts = [customer, model, qty ? `×${qty}` : ''].filter(Boolean)
  return parts.join('  ')
}

onMounted(async () => {
  currentUsername.value = readUsername()
  await loadRevisionRecords()
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  if (tab === 'revision' && revisionRecords.value.length > 0) {
    activeTab.value = 'revision'
  }
})
</script>

<style scoped lang="scss">
.page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 32px 32px 24px;
  box-sizing: border-box;
  overflow: auto;
  background: var(--yamato-color-bg-light);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-shrink: 0;
}

.page-header__left {
  display: flex;
  align-items: center;
  gap: 18px;
}

.page-header__title {
  margin: 0;
  font-family: var(--yamato-font-display);
  font-size: 34px;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: 0;
  color: var(--yamato-color-text-primary);
}

.page__tabs {
  display: flex;
  gap: 8px;
  align-items: center;
}

.page__tab {
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid transparent;
  background: transparent;
  font-size: 14px;
  color: var(--yamato-color-text-secondary);
  cursor: pointer;
  border-radius: var(--yamato-radius-md);
  transition: all 0.2s ease;

  &:hover {
    border-color: var(--yamato-color-border-subtle);
    background: var(--yamato-color-surface-alt);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }

  &--active {
    color: var(--yamato-color-accent);
    border-color: rgba(201, 100, 66, 0.34);
    background: var(--yamato-color-accent-soft);
    font-weight: 600;
  }

  &--badge {
    position: relative;
    font-weight: 600;
    color: #e67e22;
    border-color: rgba(230, 126, 34, 0.3);
    background: rgba(230, 126, 34, 0.08);
  }
}

.page__content {
  background: #ffffff;
  border-radius: var(--yamato-radius-lg);
  box-shadow: var(--yamato-shadow-card);
  padding: 28px;
  flex: 1;
  display: flex;
  justify-content: center;
}

.form {
  width: 100%;
  max-width: 980px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-revision-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--yamato-radius-sm);
  background: rgba(230, 126, 34, 0.1);
  border: 1px solid rgba(230, 126, 34, 0.28);
  color: #b35c12;
  font-size: 13px;
}

.form-revision-banner__cancel {
  flex-shrink: 0;
  min-height: 28px;
  padding: 0 12px;
  border: 1px solid rgba(230, 126, 34, 0.35);
  border-radius: var(--yamato-radius-pill);
  background: #fff;
  color: #b35c12;
  font-size: 12px;
  cursor: pointer;

  &:hover {
    background: rgba(230, 126, 34, 0.08);
  }
}

.form-section {
  border-radius: var(--yamato-radius-sm);
  background: #ffffff;
  padding: 16px 18px 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.form-section__title {
  font-size: 17px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--yamato-color-text-primary);
  margin-bottom: 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--yamato-color-border-subtle);
}

.form-section__hint {
  margin: -6px 0 14px;
  font-size: 13px;
  color: var(--yamato-color-text-secondary);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px 20px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-field__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--yamato-color-text-secondary);
  white-space: nowrap;
}

.form-field__input {
  min-height: 36px;
  padding: 0 11px;
  border-radius: var(--yamato-radius-sm);
  border: 1px solid var(--yamato-color-border-subtle);
  background: var(--yamato-color-surface);
  font-size: 14px;
  color: var(--yamato-color-text-primary);
  width: 100%;
  box-sizing: border-box;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;

  &:focus {
    outline: none;
    border-color: var(--yamato-color-accent);
    box-shadow: var(--yamato-focus-ring);
  }

  &::placeholder {
    color: var(--yamato-color-text-muted);
  }
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.form-actions__reset,
.form-actions__submit {
  min-height: 36px;
  padding: 0 18px;
  border-radius: var(--yamato-radius-sm);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s ease, opacity 0.2s ease, box-shadow 0.2s ease;

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }
}

.form-actions__reset {
  border: 1px solid var(--yamato-color-border-subtle);
  background: var(--yamato-color-surface);
  color: var(--yamato-color-text-primary);

  &:hover {
    background: var(--yamato-color-surface-alt);
  }
}

.form-actions__submit {
  border: none;
  background: var(--yamato-color-accent);
  color: #ffffff;

  &:hover:not(:disabled) {
    background: var(--yamato-color-accent-hover);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.records {
  width: 100%;
  max-width: 980px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.records__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.records__refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0 14px;
  border-radius: var(--yamato-radius-sm);
  border: 1px solid var(--yamato-color-border-subtle);
  background: var(--yamato-color-surface);
  color: var(--yamato-color-text-primary);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s ease, box-shadow 0.2s ease;

  &:hover:not(:disabled) {
    background: var(--yamato-color-surface-alt);
  }

  &:focus-visible {
    outline: none;
    box-shadow: var(--yamato-focus-ring);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.records__refresh-icon {
  flex-shrink: 0;
  transition: transform 0.4s linear;

  &--spinning {
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.records__count {
  font-size: 14px;
  color: var(--yamato-color-text-secondary);
}

.records__loading {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.records__loading-dots {
  display: flex;
  gap: 6px;

  span {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--yamato-color-accent);
    animation: dot-bounce 1.2s ease-in-out infinite;

    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}

@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.records__empty {
  padding: 48px 0;
  text-align: center;
  font-size: 14px;
  color: var(--yamato-color-text-muted);
}

.records__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.record-card {
  border-radius: var(--yamato-radius-sm);
  border: 1px solid var(--yamato-color-border-subtle);
  background: var(--yamato-color-surface);
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  transition: box-shadow 0.2s ease, border-color 0.2s ease;

  &:hover {
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.12);
  }

  &--expanded {
    border-color: rgba(201, 100, 66, 0.4);
  }

  &--pending {
    border-left: 3px solid var(--yamato-color-warning);
  }

  &--approved {
    border-left: 3px solid var(--yamato-color-success);
  }

  &--rejected {
    border-left: 3px solid var(--yamato-color-danger);
  }

  &--pending-revision {
    border-left: 3px solid #e67e22;
  }
}

.record-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}

.record-card__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.record-card__time,
.record-card__uploader {
  font-size: 12px;
  color: var(--yamato-color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.record-card__uploader {
  background: #ebe8de;
  padding: 1px 7px;
  border-radius: var(--yamato-radius-pill);
}

.record-card__summary {
  font-size: 13px;
  color: var(--yamato-color-text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-card__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 12px;
}

.record-card__chevron {
  flex-shrink: 0;
  color: var(--yamato-color-text-muted);
  transition: transform 0.2s ease;

  .record-card--expanded & {
    transform: rotate(180deg);
  }
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 10px;
  border-radius: var(--yamato-radius-pill);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;

  &--pending {
    background: var(--yamato-color-warning-soft);
    color: var(--yamato-color-warning);

    .status-badge__dot {
      background: var(--yamato-color-warning);
    }
  }

  &--approved {
    background: var(--yamato-color-success-soft);
    color: var(--yamato-color-success);

    .status-badge__dot {
      background: var(--yamato-color-success);
    }
  }

  &--rejected {
    background: var(--yamato-color-danger-soft);
    color: var(--yamato-color-danger);

    .status-badge__dot {
      background: var(--yamato-color-danger);
    }
  }

  &--pending-revision {
    background: rgba(230, 126, 34, 0.12);
    color: #e67e22;

    .status-badge__dot {
      background: #e67e22;
    }
  }
}

.status-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.action-buttons {
  display: flex;
  gap: 8px;
  align-items: center;
}

.approve-btn {
  height: 26px;
  padding: 0 12px;
  border-radius: var(--yamato-radius-pill);
  border: none;
  background: var(--yamato-color-success-soft);
  color: var(--yamato-color-success);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, opacity 0.15s ease;
  white-space: nowrap;

  &:hover:not(:disabled) {
    background: rgba(47, 158, 68, 0.24);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
}

.reject-btn {
  color: var(--yamato-color-danger);
  background: var(--yamato-color-danger-soft);

  &:hover:not(:disabled) {
    background: rgba(196, 59, 47, 0.2);
  }
}

.revise-btn {
  color: #e67e22;
  background: rgba(230, 126, 34, 0.12);

  &:hover:not(:disabled) {
    background: rgba(230, 126, 34, 0.24);
  }
}

.record-card__body {
  border-top: 1px solid var(--yamato-color-border-subtle);
  padding: 16px;
  background: var(--yamato-color-surface-alt);
}

.record-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 20px;
}

.record-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.record-field__label {
  font-size: 11px;
  color: var(--yamato-color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.record-field__value {
  font-size: 13px;
  color: var(--yamato-color-text-primary);
}

.image-upload-area {
  display: flex;
  gap: 20px;
}

.image-upload-slot {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.image-upload-slot__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--yamato-color-text-secondary);
}

.image-upload-slot__required {
  margin-left: 2px;
  color: var(--yamato-color-danger);
}

.image-upload-item {
  width: 160px;
  height: 120px;
  border: 2px dashed var(--yamato-color-border-subtle);
  border-radius: var(--yamato-radius-sm);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.2s ease;

  &:hover {
    border-color: var(--yamato-color-accent);
  }
}

.image-upload-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--yamato-color-text-muted);
  font-size: 13px;
}

.image-preview {
  position: relative;
  width: 100%;
  height: 100%;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

.image-remove-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

.record-images {
  margin-top: 12px;
  display: flex;
  gap: 12px;
}

.record-image-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--yamato-color-accent);
  text-decoration: none;

  &:hover {
    text-decoration: underline;
  }
}

@media (max-width: 1200px) {
  .form-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .record-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .page {
    padding: 24px 20px 18px;
  }

  .page-header__left {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }

  .page__content {
    padding: 20px;
  }

  .form-grid,
  .record-fields {
    grid-template-columns: 1fr;
  }

  .record-card__meta {
    flex-wrap: wrap;
  }
}
</style>
