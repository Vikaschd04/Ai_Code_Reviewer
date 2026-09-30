import type { FrameworkPack as Pack } from "../api/client";
import { plural } from "../lib/labels";
import { Disclosure } from "./Common";
import { Icon, type IconName } from "./Icon";

const VERSION: Record<string, { tone: string; label: (version: string | null) => string }> = {
  supported: { tone: "ok", label: (v) => `Version ${v ?? "?"}` },
  unsupported_version: { tone: "warn", label: (v) => `Version ${v ?? "?"} · not validated` },
  unknown_version: { tone: "neutral", label: () => "Version not declared" },
};

const STATE: Record<string, { icon: IconName; tone: string; label: string }> = {
  available: { icon: "check", tone: "ok", label: "Covered" },
  partial: { icon: "alert", tone: "warn", label: "Partly covered" },
  unavailable: { icon: "x", tone: "neutral", label: "Not covered" },
};

function PackCard({ pack }: { pack: Pack }) {
  const version = VERSION[pack.version_status] ?? VERSION.unknown_version;
  const relations = Object.values(pack.relations).reduce((sum, count) => sum + count, 0);
  const components = Object.values(pack.components).reduce((sum, count) => sum + count, 0);
  return (
    <li className="check-card" data-testid={`framework-${pack.id}`}>
      <div className="check-head">
        <h3 className="check-name">{pack.name}</h3>
        <div className="row">
          {version ? (
            <span className={`badge badge-${version.tone}`} data-testid="framework-version">
              {version.label(pack.version)}
            </span>
          ) : null}
          {pack.status === "experimental" ? (
            <span className="badge badge-neutral" title="Not yet reviewed by a platform expert">
              Experimental
            </span>
          ) : null}
        </div>
      </div>
      <p className="check-desc">
        {plural(components, "component")} and {plural(relations, "connection")} mapped from its
        configuration.
      </p>
      <ul className="capability-list plain-list">
        {pack.capabilities.map((capability) => {
          const state = STATE[capability.state] ?? STATE.unavailable;
          if (!state) return null;
          return (
            <li key={capability.id} data-state={capability.state} title={capability.detail}>
              <span className={`status-icon status-${state.tone}`}>
                <Icon name={state.icon} size={13} />
                <span className="visually-hidden">{state.label}</span>
              </span>
              <span>
                {capability.label}
                <span className="muted small"> · {capability.detail}</span>
              </span>
            </li>
          );
        })}
      </ul>
      {pack.notes.length > 0 ? (
        <ul
          className="plain-list small secondary stack stack-xs wrap-anywhere"
          data-testid="framework-notes"
        >
          {pack.notes.slice(0, 5).map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      <Disclosure summary="Technical details">
        <dl className="kv small">
          <dt>Supported</dt>
          <dd>{pack.supported_versions}</dd>
          <dt>Version read from</dt>
          <dd className="mono">{pack.version_evidence ?? "—"}</dd>
          <dt>Adapter</dt>
          <dd className="mono">{pack.adapter}</dd>
          <dt>Rules</dt>
          <dd className="mono wrap-anywhere">{pack.rules.join(", ")}</dd>
          <dt>Connections</dt>
          <dd className="mono wrap-anywhere">
            {Object.entries(pack.relations)
              .map(([name, count]) => `${name} ${String(count)}`)
              .join(", ") || "—"}
          </dd>
        </dl>
      </Disclosure>
    </li>
  );
}

/** Platform packs detected in an upload: version, what is covered and what is not. */
export function FrameworkPanel({ packs }: { packs: Pack[] }) {
  if (packs.length === 0) return null;
  return (
    <section className="card stack" aria-labelledby="frameworks-title" data-testid="frameworks">
      <div className="stack stack-xs">
        <h2 id="frameworks-title" className="card-title">
          <Icon name="box" size={16} /> Platform support
        </h2>
        <p className="card-sub">
          What refactorX understands about the platforms this code is built on. It reads their
          configuration as data; nothing is built, deployed or run.
        </p>
      </div>
      <ul className="check-list framework-list">
        {packs.map((pack) => (
          <PackCard key={pack.id} pack={pack} />
        ))}
      </ul>
    </section>
  );
}
