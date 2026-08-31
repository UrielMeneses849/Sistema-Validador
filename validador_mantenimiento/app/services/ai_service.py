from __future__ import annotations


class AIService:
    """Contrato reservado para una futura evaluación de evidencia con IA."""

    def analyze(self, _payload: object) -> None:
        raise NotImplementedError("El análisis con IA no forma parte de la Fase 1.")

