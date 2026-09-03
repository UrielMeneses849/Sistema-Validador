import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import vm from 'node:vm'

const source = await readFile(new URL('../../validador_mantenimiento/frontend/js/maintenance-analysis-api.js', import.meta.url), 'utf8')
const context = { window: {}, fetch, FormData, Error, Promise, Array, Math, Number, Set }
vm.createContext(context)
vm.runInContext(source, context)
const client = context.window.MaintenanceAnalysisAPI

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
