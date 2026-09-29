import type { SVGProps } from "react";

const PATHS = {
  dashboard: "M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z",
  projects: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  upload: "M12 16V4m0 0-5 5m5-5 5 5M4 20h16",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3zm0 3h18l-2 9H5z",
  scan: "M4 7V5a1 1 0 0 1 1-1h2m10 0h2a1 1 0 0 1 1 1v2m0 10v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M4 12h16",
  bug: "M8 8a4 4 0 0 1 8 0v8a4 4 0 0 1-8 0zM4 12h4m8 0h4M5 7l3 2m11-2-3 2M5 18l3-2m11 2-3-2",
  graph:
    "M6 6a2 2 0 1 0 0 .01M18 6a2 2 0 1 0 0 .01M12 18a2 2 0 1 0 0 .01M7.5 7.5l3.5 8.5m5.5-8.5L13 16",
  sparkles: "M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2 2m8 8 2 2M6 18l2-2m8-8 2-2",
  wrench: "M14.7 6.3a4 4 0 0 1-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 1 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z",
  branch: "M6 3v12m0 0a3 3 0 1 0 0 .01M18 9a3 3 0 1 0 0-.01M18 9c0 4-6 3-12 6",
  sun: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5",
  logout: "M15 17l5-5-5-5m5 5H9m3 9H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h7",
  check: "M5 12l5 5 9-10",
  x: "M6 6l12 12M18 6 6 18",
  alert:
    "M12 8v5m0 3h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0",
  info: "M12 11v6m0-10h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20",
  copy: "M9 9h10v10H9zM5 15V5h10",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  file: "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zm0 0v5h5",
  terminal: "M4 17l6-5-6-5m8 12h8",
  play: "M7 4v16l13-8z",
  download: "M12 4v12m0 0-5-5m5 5 5-5M4 20h16",
  box: "M21 8l-9-5-9 5v8l9 5 9-5zM3 8l9 5 9-5M12 13v8",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  code: "M8 8l-4 4 4 4m8-8 4 4-4 4m-3-11-2 14",
  clock: "M12 7v5l3 2m7-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  chevron: "M9 6l6 6-6 6",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6m7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.3 7.3 0 0 0-2-1.2L14.5 3h-4l-.4 2.6a7.3 7.3 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.3 7.3 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7.3 7.3 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2",
  users:
    "M16 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8m13 10v-2a4 4 0 0 0-3-3.9M16 2.1a4 4 0 0 1 0 7.8",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 18,
  ...rest
}: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** refactorX mark: a hexagon (a unit of code) crossed by a gradient X (refactoring). */
export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="rx-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#38bdf8" />
          <stop offset="0.55" stopColor="#818cf8" />
          <stop offset="1" stopColor="#c084fc" />
        </linearGradient>
      </defs>
      <path
        d="M20 2.5 35.5 11.25v17.5L20 37.5 4.5 28.75v-17.5z"
        fill="none"
        stroke="url(#rx-grad)"
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <path
        d="M13.5 13.5 26.5 26.5M26.5 13.5l-4.4 4.4m-4.2 4.2-4.4 4.4"
        fill="none"
        stroke="url(#rx-grad)"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Product wordmark: "refactor" in text colour, "X" in the brand gradient. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={`wordmark ${className ?? ""}`}>
      refactor<span className="gradient-text">X</span>
    </span>
  );
}
