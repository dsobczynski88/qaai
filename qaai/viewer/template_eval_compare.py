"""HTML template for the actual-vs-predicted eval diff viewer.

Renders merged comparison records produced by ``qaai.eval.compare`` — per record the
graph inputs, the actual vs predicted overall verdict + rubric cells (deviations
highlighted), and a raw actual_output vs predicted_output drill-down.

Placeholders: {{TITLE}}, {{SOURCE}}, {{RUN_KEY}}, {{DATA}}, {{LOG}}, {{REVIEW_TYPE}}.
"""

from qaai.viewer._loader import load_template

EVAL_COMPARE_TEMPLATE = load_template("eval_compare", "Actual vs Predicted")
