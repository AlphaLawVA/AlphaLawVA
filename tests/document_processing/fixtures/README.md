# Synthetic PDF fixture

`synthetic_jeonse_contract_v01.pdf` is the existing five-page synthetic
text-PDF contract previously stored under `output/pdf/`. It is not an actual
signed contract or an acquaintance's document. Its pages are labelled as
synthetic test material. Keep it with the tests so PDF parsing and extraction
are exercised in CI without external files, OCR services, or network access.

This fixture covers the earlier text-PDF extractor, not the handwritten
three-page Google OCR replay pipeline. Do not replace it with a real document.
The integration test must fail rather than skip when this file is missing.
