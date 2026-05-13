---
name: fastapi-frontend-builder
description: Builds a simple front-end for an AI application that uses FastAPI on the backend. Use this whenever the user wants a React or TypeScript UI that uploads Excel or Word files, lets the user choose from a dropdown mapped to backend endpoints, shows upload or job progress, and downloads the generated output file.
license: Proprietary
compatibility: Claude Code or Agent Skills environments with filesystem access. Assumes the target project can use React, TypeScript, Vite, and standard browser APIs or Axios.
metadata:
  owner: internal
  category: frontend
  framework: react-typescript-vite
---

# FastAPI Front-End Builder

You are a specialized implementation skill for creating a lightweight front-end for AI applications that use FastAPI as the backend.

Your job is to generate a clean, minimal, working front-end scaffold that supports:

1. Excel or Word document uploads
2. A dropdown whose options map to specific FastAPI endpoints
3. A progress indicator
4. A downloadable output file returned by the backend

## When to use this skill

Use this skill when the user asks for any of the following:

- a UI for a FastAPI backend
- a front-end that uploads files to an API
- a simple AI app front-end
- a file-processing interface with upload, progress, and download
- a React front-end for AI endpoints
- a browser UI for endpoints that accept Excel or Word files

## Default implementation assumptions

Unless the user explicitly specifies another stack, default to:

- React
- TypeScript
- Vite
- Functional components
- Minimal CSS or utility-first styling if already present
- Axios for file upload progress
- Blob download handling in browser
- Environment variable for API base URL

Use these defaults:

- Front-end app name: `ai-file-frontend`
- API base URL env var: `VITE_API_BASE_URL`
- Supported upload types:
  - `.xlsx`
  - `.xls`
  - `.docx`
  - `.doc`

## Primary objective

Generate a front-end that is easy to run, easy to modify, and easy to align with a FastAPI backend.

The generated UI must include:

- a file picker restricted to Excel and Word documents
- a dropdown selector that maps a label to an endpoint path
- a submit button
- a progress bar
- a status message area
- a download button or automatic download when the response file is ready

## Required behavior

### 1) File upload support

The app must allow uploading one file at a time.

Accepted file extensions:

- `.xlsx`
- `.xls`
- `.docx`
- `.doc`

Validate on the client:

- file is present
- file extension is allowed
- endpoint is selected before submit

### 2) Endpoint dropdown

The UI must contain a dropdown where each visible option corresponds to a backend route.

If the user provides endpoint names, use them exactly.

If the user does not provide endpoint names, create a configuration object with placeholder examples like:

- `Summarize Excel` -> `/api/process/excel-summary`
- `Analyze Workbook` -> `/api/process/excel-analysis`
- `Extract Word Insights` -> `/api/process/word-insights`
- `Rewrite Word Document` -> `/api/process/word-rewrite`

Represent endpoint options as structured configuration, not hardcoded branch logic all over the UI.

Preferred shape:

```ts
type EndpointOption = {
  id: string;
  label: string;
  endpoint: string;
  acceptedTypes: string[];
  method?: "POST";
  responseMode?: "direct-file" | "job-then-download";
}