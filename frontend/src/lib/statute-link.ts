// 국가법령정보센터의 "법령명/조문" 주소 형식(https://www.law.go.kr/법령/법령명/제N조)으로 조문 원문 링크를 만든다.
// 법령명은 주소에서 띄어쓰기 없이 쓴다("주택임대차보호법 시행령" -> "주택임대차보호법시행령").
export function statuteOfficialUrl(lawName: string, articleLabel: string): string {
  const law = lawName.replace(/\s+/g, "");
  const article = articleLabel.replace(/\s+/g, "");
  if (!law) return "";
  const path = article ? `${law}/${article}` : law;
  return `https://www.law.go.kr/법령/${path.split("/").map(encodeURIComponent).join("/")}`;
}
