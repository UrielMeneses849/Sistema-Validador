import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(new URL('../../validador_mantenimiento/frontend/js/maintenance-analysis-api.js', import.meta.url), 'utf8')
const context = { window: {}, fetch, FormData, Error, Promise, Array, Math, Number, Set }
vm.createContext(context)
vm.runInContext(source, context)
const client = context.window.MaintenanceAnalysisAPI

test('reintentar ejecuta OCR otra vez en lugar de devolver el resultado vacío almacenado', async () => {
  const originalRequest = client.request
  const paths = []
  try {
    client.request = async (path) => { paths.push(path); return { service_events: [] } }
    const item = { id: 'photo', documentId: 14, file: { name: 'foto.jpg', type: 'image/jpeg' } }
    await client.analyzeOne({ vehicleId: 1, item })
    await client.analyzeOne({ vehicleId: 1, item: { ...item, reprocess: true } })
    assert.deepEqual(paths, ['/documents/14/analyze', '/documents/14/analyze?force=true'])
  } finally { client.request = originalRequest }
})

test('la captura manual conserva el documento original y guarda ambos campos', async () => {
  const originalRequest = client.request
  try {
    client.request = async (path, options) => {
      assert.equal(path, '/service-events')
      assert.equal(options.method, 'POST')
      assert.deepEqual(JSON.parse(options.body), { document_id: 42, service_date: '2025-10-24', mileage_km: 88093, resets_maintenance_interval: true })
      return { id: 77, document_id: 42, service_date: '2025-10-24', mileage_km: 88093 }
    }
    const saved = await client.createRecord(42, { service_date: '2025-10-24', mileage_km: 88093 })
    assert.equal(saved.document_id, 42)
    assert.equal(saved.id, 77)
  } finally { client.request = originalRequest }
})

test('separa PDF e imágenes y acepta archivos sin MIME identificable', () => {
  const jpeg = { name: 'servicio.JPG', type: '', size: 1500 }
  const pdf = { name: 'historial.pdf', type: 'application/pdf', size: 1500 }
  assert.equal(client.validateFiles([jpeg], 'image'), '')
  assert.equal(client.validateFiles([pdf], 'pdf'), '')
  assert.match(client.validateFiles([pdf], 'image'), /Validador de mantenimientos PDF/)
  assert.match(client.validateFiles([jpeg], 'pdf'), /Validador de mantenimientos imágenes/)
  assert.match(client.validateFiles([{ name: 'falso.jpg', type: 'application/pdf', size: 1500 }], 'image'), /no es una imagen/)
})

test('rechaza lotes excesivos, archivos vacíos y archivos de más de 10 MB', () => {
  const image = { name: 'servicio.png', type: 'image/png', size: 1500 }
  assert.match(client.validateFiles(Array.from({ length: 16 }, () => image)), /hasta 15/)
  assert.match(client.validateFiles([{ ...image, size: 0 }]), /vacío/)
  assert.match(client.validateFiles([{ name: 'historial.pdf', type: 'application/pdf', size: 10 * 1024 * 1024 + 1 }], 'pdf'), /10 MB/)
})

test('conserva resultados exitosos cuando otro archivo falla', async () => {
  client.analyzeOne = async ({ item }) => {
    if (item.id === 'bad') throw Object.assign(new Error('OCR no disponible'), { itemId: item.id })
    return { itemId: item.id, documentId: 10, records: [{ id: 'event-1' }] }
  }
  const outcomes = await client.analyzeFiles({
    vehicleId: 1,
    items: [
      { id: 'good', file: { name: 'good.jpg' } },
      { id: 'bad', file: { name: 'bad.jpg' } },
    ],
    concurrency: 2,
  })
  assert.equal(outcomes.length, 2)
  assert.equal(outcomes[0].status, 'fulfilled')
  assert.equal(outcomes[1].status, 'rejected')
  assert.deepEqual(outcomes[0].value.records, [{ id: 'event-1' }])
})

test('respeta el límite de quince imágenes', async () => {
  await assert.rejects(
    client.analyzeFiles({ vehicleId: 1, items: Array.from({ length: 16 }, (_, index) => ({ id: String(index), file: { name: `${index}.jpg` } })) }),
    /máximo es 15/,
  )
})

test('ofrece el original aunque el OCR no haya generado recortes de evidencia', () => {
  const records = client.toMaintenanceRecords({ service_events: [{
    id: 8, document_id: 5, service_date: null, mileage_km: null,
    requires_human_review: true, user_confirmed: false, confidence: 'low',
    field_evidence: {},
  }] }, 'mantenimiento.jpg', 'image/jpeg')
  assert.equal(records[0].reviewStatus, 'review')
  assert.equal(records[0].originalUrl, '/api/documents/5/file')
  assert.equal(records[0].dateCropUrl, null)
})

test('una corrección incompleta o inconsistente conserva revisión pendiente', () => {
  const record = client.toMaintenanceRecords({ service_events: [{
    id: 8, document_id: 5, service_date: '2024-01-06', mileage_km: 40123,
    requires_human_review: true, user_confirmed: true, confidence: 'high',
    field_evidence: { manually_verified: true },
  }] }, 'mantenimiento.jpg')[0]
  assert.equal(record.reviewStatus, 'review')
  assert.equal(record.manuallyVerified, true)
})

test('guarda fecha y kilometraje juntos y conserva el enlace al original', async () => {
  const originalRequest = client.request
  const record = { eventId: 8, documentId: 5, originalUrl: '/api/documents/5/file', date: '', mileage: '' }
  try {
    client.request = async (path, options) => {
      assert.equal(path, '/service-events/8')
      assert.equal(options.method, 'PUT')
      assert.deepEqual(JSON.parse(options.body), { service_date: '2024-01-06', mileage_km: 40123 })
      return { service_date: '2024-01-06', mileage_km: 40123, requires_human_review: false, field_evidence: {} }
    }
    const corrected = await client.updateRecord(record, { service_date: '2024-01-06', mileage_km: 40123 })
    assert.equal(corrected.reviewStatus, 'manual')
    assert.equal(corrected.date, '2024-01-06')
    assert.equal(corrected.mileage, 40123)
    assert.equal(corrected.originalUrl, '/api/documents/5/file')
  } finally { client.request = originalRequest }
})

test('si falla el guardado conserva los valores anteriores', async () => {
  const originalRequest = client.request
  const record = { eventId: 8, date: '2024-01-06', mileage: 40123 }
  try {
    client.request = async () => { throw new Error('No se pudo guardar') }
    await assert.rejects(client.updateRecord(record, { mileage_km: 45000 }), /No se pudo guardar/)
    assert.deepEqual(record, { eventId: 8, date: '2024-01-06', mileage: 40123 })
  } finally { client.request = originalRequest }
})

test('guardar un solo campo no oculta una revisión todavía incompleta', async () => {
  const originalRequest = client.request
  try {
    client.request = async () => ({ service_date: null, mileage_km: 9000, requires_human_review: true, field_evidence: {} })
    const record = await client.updateRecord({ eventId: 8, date: '', mileage: '' }, { mileage_km: 9000 })
    assert.equal(record.reviewStatus, 'review')
    assert.equal(record.date, '')
    assert.equal(record.mileage, 9000)
  } finally { client.request = originalRequest }
})
