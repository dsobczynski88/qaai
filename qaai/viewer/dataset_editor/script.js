// Dataset Studio editor entry point.
//
// Everything type-specific reaches this page as data: the input pane is built from
// {{INPUT_SCHEMA}} (the projected Pydantic row model) and the output pane from
// {{CONFIG}} (the eval spec). There is deliberately nothing here that names a
// reviewer, a rubric code, or a field — adding a fourth dataset type requires no
// change to this file.
//
// The runtime lives in common/editor.js, which is concatenated ahead of this file.

initEditor();
