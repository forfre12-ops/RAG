from __future__ import annotations
from uuid import uuid4

from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
from lloydk.modules.m5_inference.pipeline import InferencePipeline


class ClassifyService:
    _instance: "ClassifyService | None" = None

    def __init__(self):
        self.preprocess = PreprocessPipeline()
        self.inference = InferencePipeline()

    @classmethod
    def get_instance(cls) -> "ClassifyService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def classify(self, req: ClassifyRequest) -> ClassifyResponse:
        if req.text_already_preprocessed:
            cleaned = req.content
        else:
            cleaned = self.preprocess.run_text(req.content)

        pred = self.inference.run(
            text=cleaned,
            use_rag=req.use_rag,
            rag_namespace=req.rag_namespace,
            metadata=req.metadata or {},
            return_evidence=req.return_evidence,
        )

        return ClassifyResponse(
            inference_id=uuid4(),
            doc_id=req.doc_id,
            label=pred.label,
            confidence=pred.confidence,
            scores=pred.scores,
            evaluation_factors=pred.factors,
            evidence=pred.evidence,
            rag_context_used=pred.rag_context,
            model_version=pred.model_version,
            elapsed_ms=0,
            status="staging",
            warnings=pred.warnings,
        )
