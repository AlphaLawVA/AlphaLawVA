export type DocumentMode = "contract" | "both" | "registry";

export type ReviewStatus = "safe" | "check";

export type LegalBasis = {
  law: string;
  article: string;
  excerpt: string;
  explanation: string;
  url: string;
};

export type CaseBasis = {
  title: string;
  caseNumber: string;
  precedentId: string;
  summary: string;
  easySummary?: string;
  matchedReason?: string;
  court?: string;
  decisionDate?: string;
  judgmentType?: string;
  evidenceChunks?: Array<{ section: string; content: string }>;
  highlightChunks?: Array<{ section: string; startOffset: number; endOffset: number }>;
  fullText?: {
    order?: string;
    claim?: string;
    lowerCourt?: string;
    reason?: string;
  };
  url: string;
};

export type ClauseReview = {
  id: string;
  title: string;
  original: string;
  status: ReviewStatus;
  reason: string;
  revision: string;
  negotiation: string;
  statutes: LegalBasis[];
  precedents: CaseBasis[];
};

export type RegistryReview = {
  id: string;
  title: string;
  value: string;
  status: ReviewStatus;
  summary: string;
  action?: string;
};

export type FieldPriority = "needs-confirmation" | "priority" | "automatic";

export type FieldStatus =
  | "automatic"
  | "needs-confirmation"
  | "user-confirmed"
  | "user-modified"
  | "confirmed-empty";

export type SourceAnchor = {
  page: number;
  label: string;
};

export type SnapshotField = {
  id: string;
  label: string;
  value: string;
  group: string;
  priority: FieldPriority;
  status: FieldStatus;
  required?: boolean;
  multiline?: boolean;
  source: SourceAnchor;
};

export type ConfirmedContractSnapshot = {
  contractType: "전세" | "월세" | "매매";
  fields: SnapshotField[];
};

export type RegistryRight = {
  id: string;
  section: "갑구" | "을구";
  rank: string;
  purpose: string;
  receivedDate: string;
  holder: string;
  debtor?: string;
  maximumAmount?: string;
  status: "active" | "cancelled" | "unclear";
  source: SourceAnchor;
};

export type ConfirmedRegistrySnapshot = {
  fields: SnapshotField[];
  rights: RegistryRight[];
};
