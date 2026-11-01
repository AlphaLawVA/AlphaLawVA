"use client";

import Link from "next/link";
import { useState } from "react";
import { Menu, X } from "lucide-react";
import { logoutAction } from "@/app/auth/actions";
import styles from "./site-header.module.css";

const navItems = [
  { href: "/contract-review", label: "계약서 검토" },
  { href: "/clause-generator", label: "특약 생성" },
  { href: "/precedent-search", label: "판례 검색" },
  { href: "/certified-letter", label: "내용증명 생성" },
];

export function SiteHeader({ authenticated }: { authenticated: boolean }) {
  const [open, setOpen] = useState(false);

  const accountLinks = authenticated
    ? [{ href: "/mypage", label: "마이페이지" }]
    : [{ href: "/login", label: "로그인" }];

  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link className={styles.brand} href="/" aria-label="AlphaLawVA 홈">
          AlphaLawVA
        </Link>
        <nav className={styles.nav} aria-label="주요 기능">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href}>
              {item.label}
            </Link>
          ))}
          <span className={styles.navDivider} aria-hidden="true" />
          {accountLinks.map((item) => (
            <Link key={item.href} href={item.href}>
              {item.label}
            </Link>
          ))}
          {authenticated && (
            <form action={logoutAction}>
              <button className={styles.logoutButton} type="submit">
                로그아웃
              </button>
            </form>
          )}
        </nav>
        <button
          className={styles.menuButton}
          type="button"
          aria-label={open ? "메뉴 닫기" : "메뉴 열기"}
          aria-expanded={open}
          aria-controls="mobile-navigation"
          title={open ? "메뉴 닫기" : "메뉴 열기"}
          onClick={() => setOpen((current) => !current)}
        >
          {open ? <X size={21} aria-hidden="true" /> : <Menu size={21} aria-hidden="true" />}
        </button>
      </div>
      {open && (
        <nav className={styles.mobileNav} id="mobile-navigation" aria-label="모바일 주요 기능">
          {navItems.map((item) => (
            <Link key={item.href} href={item.href} onClick={() => setOpen(false)}>
              {item.label}
            </Link>
          ))}
          {accountLinks.map((item) => (
            <Link key={item.href} href={item.href} onClick={() => setOpen(false)}>
              {item.label}
            </Link>
          ))}
          {authenticated && (
            <form action={logoutAction}>
              <button className={styles.mobileLogoutButton} type="submit">
                로그아웃
              </button>
            </form>
          )}
        </nav>
      )}
    </header>
  );
}

