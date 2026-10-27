export type PreviewDocumentKind = "contract" | "registry";

type PreviewDocument = {
  name: string;
  url: string;
  mimeType: string;
};

const previews = new Map<PreviewDocumentKind, PreviewDocument>();

export function setDocumentPreview(kind: PreviewDocumentKind, file: File) {
  const previous = previews.get(kind);
  if (previous) URL.revokeObjectURL(previous.url);
  previews.set(kind, {
    name: file.name,
    url: URL.createObjectURL(file),
    mimeType: file.type,
  });
}

export function getDocumentPreview(kind: PreviewDocumentKind) {
  return previews.get(kind);
}

export function clearDocumentPreviews() {
  previews.forEach((preview) => URL.revokeObjectURL(preview.url));
  previews.clear();
}
