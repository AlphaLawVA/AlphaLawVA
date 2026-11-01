import Link from "next/link";
import {
  ArrowRight,
  FileCheck2,
  FilePenLine,
  Landmark,
  SearchCheck,
  ShieldCheck,
} from "lucide-react";
import styles from "./page.module.css";

const features = [
  {
    href: "/contract-review",
    icon: FileCheck2,
    eyebrow: "계약 전",
    title: "계약서 검토",
    description: "계약서와 등기사항증명서를 함께 확인하고, 놓치기 쉬운 조항과 권리관계를 짚어봅니다.",
  },
  {
    href: "/clause-generator",
    icon: FilePenLine,
    eyebrow: "계약 중",
    title: "특약 생성",
    description: "내 상황과 요구사항을 여러 개 입력하고 협상에 사용할 특약 초안을 만듭니다.",
  },
  {
    href: "/precedent-search",
    icon: SearchCheck,
    eyebrow: "분쟁 이해",
    title: "판례 검색",
    description: "부동산 분쟁 상황을 자연어로 검색하고 비슷한 쟁점의 판례를 쉬운 설명과 함께 살펴봅니다.",
  },
  {
    href: "/certified-letter",
    icon: Landmark,
    eyebrow: "분쟁 대응",
    title: "내용증명 생성",
    description: "상황을 자유롭게 설명하면 필요한 사실을 확인하고 발송 전 검토할 초안을 작성합니다.",
  },
];

export default function Home() {
  return (
    <main>
      <section className={styles.hero}>
        <div className={styles.heroInner}>
          <div className={styles.heroCopy}>
            <p className={styles.eyebrow}>부동산 계약을 이해하는 법률 파트너</p>
            <h1>AlphaLawVA</h1>
            <p className={styles.lead}>
              계약서를 읽는 순간부터 문제가 생긴 뒤 대응하는 순간까지,
              필요한 정보를 확인하고 다음 행동을 결정할 수 있도록 돕습니다.
            </p>
            <div className={styles.heroActions}>
              <Link className={styles.primaryButton} href="/contract-review">
                계약서 검토 시작
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <a className={styles.textLink} href="#features">전체 기능 보기</a>
            </div>
          </div>

          <div className={styles.flowPreview} aria-label="계약서 검토 화면 미리보기">
            <div className={styles.previewTop}>
              <span>계약 검토 결과</span>
              <span className={styles.previewStatus}>4개 확인</span>
            </div>
            <div className={styles.previewBody}>
              <div className={styles.previewDocument}>
                <span /><span /><span /><span /><span />
              </div>
              <div className={styles.previewChecks}>
                <div><i className={styles.safeDot} />계약 당사자 일치</div>
                <div><i className={styles.checkDot} />권리관계 변경 확인</div>
                <div><i className={styles.checkDot} />원상회복 범위 확인</div>
              </div>
            </div>
            <div className={styles.previewFooter}>
              <ShieldCheck size={17} aria-hidden="true" />
              법령·판례 근거와 함께 확인합니다
            </div>
          </div>
        </div>
      </section>

      <section className={styles.features} id="features">
        <div className={styles.sectionHeading}>
          <p>계약 전부터 분쟁 대응까지</p>
          <h2>필요한 순간에 바로 사용할 수 있는 기능</h2>
        </div>
        <div className={styles.featureGrid}>
          {features.map(({ href, icon: Icon, eyebrow, title, description }) => (
            <Link className={styles.featureItem} href={href} key={title}>
              <div className={styles.featureIcon}>
                <Icon size={22} strokeWidth={1.8} aria-hidden="true" />
              </div>
              <p>{eyebrow}</p>
              <h3>{title}</h3>
              <span>{description}</span>
              <ArrowRight className={styles.featureArrow} size={19} aria-hidden="true" />
            </Link>
          ))}
        </div>
      </section>

      <section className={styles.noticeBand}>
        <ShieldCheck size={25} aria-hidden="true" />
        <div>
          <strong>중요한 계약과 분쟁은 원문과 전문가의 확인이 필요합니다.</strong>
          <p>AlphaLawVA는 AI를 활용해 부동산 문서 이해와 초안 작성을 돕는 참고용 서비스이며, 법률 자문이나 법적 판단을 대신하지 않습니다.</p>
        </div>
      </section>
    </main>
  );
}
