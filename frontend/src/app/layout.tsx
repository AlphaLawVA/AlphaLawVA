import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";
import { SiteHeader } from "@/components/site-header";
import { getAuthIdentity } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaLawVA | 부동산 계약을 이해하는 법률 파트너",
  description: "계약 검토부터 분쟁 대응까지 돕는 부동산 법률 서비스",
};

async function AuthenticatedHeader() {
  const identity = await getAuthIdentity();
  return <SiteHeader authenticated={Boolean(identity)} />;
}

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ko" data-scroll-behavior="smooth">
      <body>
        <Suspense fallback={<SiteHeader authenticated={false} />}>
          <AuthenticatedHeader />
        </Suspense>
        {children}
      </body>
    </html>
  );
}
