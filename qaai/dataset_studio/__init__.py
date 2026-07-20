"""Dataset Studio — scaffold, validate, and human-review eval datasets.

Companion to :mod:`qaai.eval`. Where ``qaai.eval`` *scores* a dataset, this package
is concerned with a dataset's life before scoring: creating the timestamped folder,
checking every row against the live Pydantic models, and serving a browser editor so
a human can accept or correct each generated sample with an append-only audit trail.

The three reviewer rubrics are never restated here. Everything type-specific is read
from two existing sources of truth:

* ``eval/specs/<component>.yaml`` (via :func:`qaai.eval.spec.load_spec`) — where the
  verdict and rubric live, which codes exist, and which are advisory.
* The reviewer graph-state ``TypedDict``s — the real field annotations, projected into
  per-row Pydantic models by :mod:`qaai.dataset_studio.registry`.

Add a field to a reviewer's state or a code to its spec and the studio follows along
with no edit here.
"""

from qaai.dataset_studio.registry import (
    DATASET_TYPES,
    DatasetTypeInfo,
    dataset_type_for,
    infer_dataset_type,
    input_row_model,
    load_type_spec,
    output_row_model,
    output_row_shape,
)
from qaai.dataset_studio.rules import (
    NA_ALLOWED,
    derive_overall_verdict,
)

__all__ = [
    "DATASET_TYPES",
    "DatasetTypeInfo",
    "dataset_type_for",
    "infer_dataset_type",
    "input_row_model",
    "load_type_spec",
    "output_row_model",
    "output_row_shape",
    "NA_ALLOWED",
    "derive_overall_verdict",
]
