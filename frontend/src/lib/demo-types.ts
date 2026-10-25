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
  court?: string;
  decisionDate?: string;
  judgmentType?: string;
  evidenceChunks?: Array<{ section: string; content: string }>;
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
