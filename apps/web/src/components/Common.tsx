import { useState, type ReactNode } from "react";

import { Icon } from "./Icon";

export function Alert({ tone, children }: { tone: "bad" | "warn" | "info"; children: ReactNode }) {
  return (
    <div className={`alert alert-${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={tone === "info" ? "info" : "alert"} />
      <div>{children}</div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

/** Heading row for a group of cards outside a card (title left, optional action right). */
export function SectionHeader({
  id,
  title,
  action,
}: {
  id: string;
  title: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="section-head">
      <h2 id={id} className="section-title">
        {title}
      </h2>
      {action}
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div className="stack" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, index) => (
        <div key={index} className="skeleton" style={{ width: `${90 - index * 15}%` }} />
      ))}
    </div>
  );
}

export function CopyBlock({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-block">
      <code aria-label={label}>{text}</code>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => {
          void navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            window.setTimeout(() => {
              setCopied(false);
            }, 1500);
          });
        }}
      >
        <Icon name={copied ? "check" : "copy"} size={14} />
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function Tabs({
  items,
  current,
}: {
  items: { id: string; label: string; href: string }[];
  current: string;
}) {
  return (
    <nav className="tabs" aria-label="Sections">
      {items.map((item) => (
        <a
          key={item.id}
          className="tab"
          href={item.href}
          aria-current={item.id === current ? "page" : undefined}
        >
          {item.label}
        </a>
      ))}
    </nav>
  );
}

export function PageHeader({
  title,
  sub,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        {eyebrow ? <div className="page-eyebrow">{eyebrow}</div> : null}
        <h1 className="page-title" tabIndex={-1}>
          {title}
        </h1>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

/** Collapsed section for details most reviewers do not need (versions, hashes, provenance). */
export function Disclosure({
  summary = "Technical details",
  children,
  testId,
  defaultOpen = false,
}: {
  summary?: ReactNode;
  children: ReactNode;
  testId?: string;
  defaultOpen?: boolean;
}) {
  return (
    <details className="disclosure" data-testid={testId} open={defaultOpen}>
      <summary>
        <Icon name="chevron" size={14} className="disclosure-chevron" />
        {summary}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
