const MaintenanceAnalysisAPI = {
  maximumFiles: 15,
  concurrency: 2,

  async request(path, options, signal) {
    const response = await fetch(`/api${path}`, { ...options, signal })
    const body = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = Array.isArray(body?.detail)
        ? body.detail.map((item) => item.msg).join(" ")
        : body?.detail || "No se pudo completar la solicitud."
      const error = new Error(detail)
      error.status = response.status
      throw error
    }
    return body
  },

  async analyzeOne({ vehicleId, item, signal }) {
    let documentId = item.documentId || null
    try {
      if (!documentId) {
        const form = new FormData()
        form.append("vehicle_id", vehicleId)
        form.append("file", item.file)
        const document = await this.request("/documents/upload", { method: "POST", body: form }, signal)
        documentId = document.id
      }
      const analysis = await this.request(`/documents/${documentId}/analyze`, { method: "POST" }, signal)
      return { itemId: item.id, documentId, analysis, records: this.toMaintenanceRecords(analysis, item.file.name, item.file.type) }
    } catch (error) {
      error.documentId = documentId
      error.itemId = item.id
      throw error
    }
  },

  toMaintenanceRecords(analysis, sourceFileName, mimeType = "") {
    return (analysis.service_events || []).map((event) => {
      const evidence = event.field_evidence || {}
      const confidence = Number(evidence.combined_confidence ?? ({ high: 0.95, medium: 0.72, low: 0.4 }[event.confidence] || 0))
      const manuallyVerified = Boolean(event.user_confirmed || evidence.manually_verified)
      return {
        id: `event-${event.id}`,
        eventId: event.id,
        documentId: event.document_id,
        date: event.service_date || "",
        mileage: event.mileage_km ?? "",
        sourceFileName,
        sourceType: mimeType === "application/pdf" || sourceFileName.toLowerCase().endsWith(".pdf") ? "pdf" : "image",
        confidence,
        reviewStatus: manuallyVerified ? "manual" : event.requires_human_review || confidence < 0.88 ? "review" : "confirmed",
        manuallyVerified,
        rawExtractedText: analysis.extracted_text || "",
        fieldEvidence: evidence,
        warnings: [...(analysis.warnings || []), ...(event.warnings || [])],
        dateCropUrl: evidence.date?.crop_id ? `/api/service-events/${event.id}/evidence/date` : null,
        mileageCropUrl: evidence.mileage_km?.crop_id ? `/api/service-events/${event.id}/evidence/mileage_km` : null,
      }
    })
  },

  async analyzeFiles({ vehicleId, items, signal, onProgress, concurrency = this.concurrency }) {
    if (items.length > this.maximumFiles) throw new Error(`El máximo es ${this.maximumFiles} archivos por análisis.`)
    const settled = new Array(items.length)
    let nextIndex = 0
    let completed = 0
    const worker = async () => {
      while (nextIndex < items.length) {
        if (signal?.aborted) return
        const index = nextIndex++
        const item = items[index]
        onProgress?.({ phase: "processing", completed, total: items.length, fileName: item.file.name, itemId: item.id })
        try {
          const value = await this.analyzeOne({ vehicleId, item, signal })
          settled[index] = { status: "fulfilled", value }
        } catch (reason) {
          settled[index] = { status: "rejected", reason }
        } finally {
          completed += 1
          onProgress?.({ phase: "completed", completed, total: items.length, fileName: item.file.name, itemId: item.id })
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => worker()))
    return settled.filter(Boolean)
  },

  async updateRecord(record, changes, signal) {
    if (!record.eventId) return record
    const event = await this.request(`/service-events/${record.eventId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }, signal)
    return {
      ...record,
      date: event.service_date || "",
      mileage: event.mileage_km ?? "",
      confidence: 1,
      reviewStatus: "manual",
      manuallyVerified: true,
      fieldEvidence: event.field_evidence,
    }
  },

  async confirmRecord(record, signal) {
    if (!record.eventId) return record
    const event = await this.request(`/service-events/${record.eventId}/confirm`, { method: "POST" }, signal)
    return {
      ...record,
      date: event.service_date || "",
      mileage: event.mileage_km ?? "",
      confidence: Number(event.field_evidence?.combined_confidence ?? 1),
      reviewStatus: event.requires_human_review ? "review" : "manual",
      manuallyVerified: true,
      fieldEvidence: event.field_evidence,
      warnings: event.warnings || [],
    }
  },

  async validateHistory(vehicleId, eventIds, signal) {
    if (!eventIds.length) return null
    return this.request("/history-validations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vehicle_id: Number(vehicleId), event_ids: eventIds }),
    }, signal)
  },
}

window.MaintenanceAnalysisAPI = MaintenanceAnalysisAPI
