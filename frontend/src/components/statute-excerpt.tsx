"use client";

import { useState } from "react";
import styles from "./statute-excerpt.module.css";

type Line = { kind: "paragraph" | "item" | "subitem" | "text"; text: string };

const COLLAPSED_LINE_COUNT = 8;
const PARAGRAPH_MARK = /^[①-⑳㉑-㉟]/;
const ITEM_MARK = /^\d+(?:의\d+)?\.\s/;
const SUBITEM_MARK = /^(?:[가-힣]\.|\d+\))\s/;

function parseStatuteText(raw: string) {
  let path = "";
  let heading = "";
  const lines: Line[] = [];
  for (const source of raw.split(/\r?\n/)) {
    const line = source.trim();
    if (!line) continue;
    if (line.startsWith("법령:") || line.startsWith("본문:")) continue;
    if (line.startsWith("구조:")) {
      path = line.slice(3).trim();
      continue;
    }
    if (line.startsWith("조문:")) {
      heading = line.slice(3).trim();
      continue;
    }
    if (PARAGRAPH_MARK.test(line)) lines.push({ kind: "paragraph", text: line });
    else if (ITEM_MARK.test(line)) lines.push({ kind: "item", text: line });
    else if (SUBITEM_MARK.test(line)) lines.push({ kind: "subitem", text: line });
    else lines.push({ kind: "text", text: line });
  }
  return { path, heading, lines };
}

export function StatuteExcerpt({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const { path, heading, lines } = parseStatuteText(text);
  const collapsible = lines.length > COLLAPSED_LINE_COUNT;
  const visible = collapsible && !expanded ? lines.slice(0, COLLAPSED_LINE_COUNT) : lines;

  return (
    <div className={styles.excerpt}>
      {path && <div className={styles.path}>{path.replace(/<[^>]*>/g, "").trim()}</div>}
      {heading && <div className={styles.heading}>{heading}</div>}
      <div className={styles.body}>
        {visible.map((line, index) => (
          <div className={`${styles.line} ${styles[line.kind]}`} key={`${index}-${line.text.slice(0, 12)}`}>{line.text}</div>
        ))}
        {collapsible && !expanded && <div className={styles.fade} aria-hidden="true" />}
      </div>
      {collapsible && (
        <button className={styles.toggle} onClick={() => setExpanded((value) => !value)} type="button">
          {expanded ? "접기" : `전체 조문 보기 (${lines.length}개 항목)`}
        </button>
      )}
    </div>
  );
}
