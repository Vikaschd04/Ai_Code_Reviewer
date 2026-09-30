/** Thin wrappers that turn contract responses into values or ApiError exceptions. */
import {
  api,
  toApiError,
  type AuthOptions,
  type Capability,
  type CoveragePage,
  type DiagnosticRun,
  type FilePage,
  type FindingDetail,
  type FindingPage,
  type GraphImpact,
  type GraphNeighborhood,
  type GraphNodePage,
  type GraphSummary,
  type Intake,
  type IntakePolicy,
  type IssueDetail,
  type IssuePage,
  type IssueTriage,
  type Principal,
  type Project,
  type ProjectOverview,
  type ReadinessReport,
  type SampleProject,
  type Scan,
  type ScanComparison,
  type Snapshot,
} from "./client";

export async function fetchPrincipal(signal?: AbortSignal): Promise<Principal> {
  const { data, error, response } = await api.GET("/v1/auth/me", { signal: signal ?? null });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchAuthOptions(signal?: AbortSignal): Promise<AuthOptions> {
  const { data, error, response } = await api.GET("/v1/auth/options", { signal: signal ?? null });
  if (data) return data;
  throw toApiError(response, error);
}

/** Sign in to the shared demo account (only when the server enables it). */
export async function startDemoSession(): Promise<void> {
  const { data, error, response } = await api.POST("/v1/auth/demo-session");
  if (!data) throw toApiError(response, error);
}

export async function signIn(token: string): Promise<void> {
  const { data, error, response } = await api.POST("/v1/auth/session", { body: { token } });
  if (!data) throw toApiError(response, error);
}

export async function signOut(): Promise<void> {
  const { response } = await api.DELETE("/v1/auth/session");
  if (!response.ok) throw toApiError(response, undefined);
}

/** Readiness returns the same report body with HTTP 503 when any dependency is not OK. */
export async function fetchReadiness(signal?: AbortSignal): Promise<ReadinessReport> {
  const { data, error, response } = await api.GET("/v1/health/ready", { signal: signal ?? null });
  if (data) return data;
  if (response.status === 503 && "checks" in error) return error;
  throw toApiError(response, error);
}

export async function fetchCapabilities(signal?: AbortSignal): Promise<Capability[]> {
  const { data, error, response } = await api.GET("/v1/capabilities", { signal: signal ?? null });
  if (data) return data.capabilities;
  throw toApiError(response, error);
}

export async function listProjects(signal?: AbortSignal): Promise<Project[]> {
  const { data, error, response } = await api.GET("/v1/projects", {
    params: { query: { limit: 100 } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function createProject(input: {
  workspaceId: string;
  name: string;
  description: string;
}): Promise<Project> {
  const { data, error, response } = await api.POST("/v1/projects", {
    body: { workspace_id: input.workspaceId, name: input.name, description: input.description },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function startDiagnostic(): Promise<DiagnosticRun> {
  const { data, error, response } = await api.POST("/v1/diagnostics/workflow-runs");
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchDiagnostic(
  workflowId: string,
  signal?: AbortSignal,
): Promise<DiagnosticRun> {
  const { data, error, response } = await api.GET("/v1/diagnostics/workflow-runs/{workflow_id}", {
    params: { path: { workflow_id: workflowId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

// -- Phase 1: intake, snapshots, scans, findings ------------------------------------------------

type Sig = AbortSignal | undefined;

export async function fetchProjectOverview(
  projectId: string,
  signal?: Sig,
): Promise<ProjectOverview> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/overview", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchProject(projectId: string, signal?: Sig): Promise<Project> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchIntakePolicy(signal?: Sig): Promise<IntakePolicy> {
  const { data, error, response } = await api.GET("/v1/intake-policy", { signal: signal ?? null });
  if (data) return data;
  throw toApiError(response, error);
}

/** Permanently delete a project with everything it holds (uploads, reviews, issues). */
export async function deleteProject(projectId: string): Promise<void> {
  const { error, response } = await api.DELETE("/v1/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  if (!response.ok) throw toApiError(response, error);
}

export async function createSampleProject(workspaceId: string): Promise<SampleProject> {
  const { data, error, response } = await api.POST("/v1/projects/sample", {
    body: { workspace_id: workspaceId },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function createIntake(projectId: string, displayName: string): Promise<Intake> {
  const { data, error, response } = await api.POST("/v1/projects/{project_id}/intakes", {
    params: { path: { project_id: projectId } },
    body: { mode: "zip_upload", display_name: displayName },
  });
  if (data) return data;
  throw toApiError(response, error);
}

async function requestUploadTicket(intakeId: string): Promise<{ upload_url: string }> {
  const { data, error, response } = await api.POST("/v1/intakes/{intake_id}/upload-ticket", {
    params: { path: { intake_id: intakeId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

/**
 * XHR upload so the UI can show real byte progress (fetch has no upload progress events).
 * The archive goes to the ticket's URL: same origin locally, the API host itself when hosted
 * (bypassing the web host's proxy); the short-lived ticket replaces cookies for that request.
 */
export async function uploadArchive(
  intakeId: string,
  file: Blob,
  onProgress: (fraction: number) => void,
): Promise<Intake> {
  const ticket = await requestUploadTicket(intakeId);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", ticket.upload_url);
    xhr.withCredentials = false;
    xhr.setRequestHeader("Content-Type", "application/zip");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onerror = () => {
      reject(new TypeError("upload failed: network error"));
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as Intake);
      } else {
        reject(toApiError(new Response(null, { status: xhr.status }), body));
      }
    };
    xhr.send(file);
  });
}

export async function finalizeIntake(intakeId: string): Promise<Intake> {
  const { data, error, response } = await api.POST("/v1/intakes/{intake_id}/finalize", {
    params: { path: { intake_id: intakeId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchIntake(intakeId: string, signal?: Sig): Promise<Intake> {
  const { data, error, response } = await api.GET("/v1/intakes/{intake_id}", {
    params: { path: { intake_id: intakeId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listIntakes(projectId: string, signal?: Sig): Promise<Intake[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/intakes", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function listSnapshots(projectId: string, signal?: Sig): Promise<Snapshot[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/snapshots", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function fetchSnapshot(snapshotId: string, signal?: Sig): Promise<Snapshot> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}", {
    params: { path: { snapshot_id: snapshotId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listFiles(
  snapshotId: string,
  query: { disposition?: string; q?: string; cursor?: string },
  signal?: Sig,
): Promise<FilePage> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/files", {
    params: {
      path: { snapshot_id: snapshotId },
      query: {
        limit: 100,
        ...(query.disposition ? { disposition: query.disposition } : {}),
        ...(query.q ? { q: query.q } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function startScan(
  projectId: string,
  snapshotId: string,
  cacheMode: "use" | "refresh" = "use",
): Promise<Scan> {
  const { data, error, response } = await api.POST("/v1/projects/{project_id}/scans", {
    params: { path: { project_id: projectId }, header: { "Idempotency-Key": crypto.randomUUID() } },
    body: { snapshot_id: snapshotId, cache_mode: cacheMode },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listScans(projectId: string, signal?: Sig): Promise<Scan[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/scans", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function fetchScan(scanId: string, signal?: Sig): Promise<Scan> {
  const { data, error, response } = await api.GET("/v1/scans/{scan_id}", {
    params: { path: { scan_id: scanId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function cancelScan(scanId: string): Promise<Scan> {
  const { data, error, response } = await api.POST("/v1/scans/{scan_id}/cancel", {
    params: { path: { scan_id: scanId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export interface FindingQuery {
  severity: string[];
  category?: string;
  engine?: string;
  q?: string;
  issueStatus?: string;
  cursor?: string;
}

export async function listFindings(
  scanId: string,
  query: FindingQuery,
  signal?: Sig,
): Promise<FindingPage> {
  const { data, error, response } = await api.GET("/v1/scans/{scan_id}/findings", {
    params: {
      path: { scan_id: scanId },
      query: {
        limit: 50,
        ...(query.severity.length ? { severity: query.severity } : {}),
        ...(query.category ? { category: [query.category] } : {}),
        ...(query.engine ? { engine: query.engine } : {}),
        ...(query.q ? { q: query.q } : {}),
        ...(query.issueStatus ? { issue_status: [query.issueStatus] } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchFinding(findingId: string, signal?: Sig): Promise<FindingDetail> {
  const { data, error, response } = await api.GET("/v1/findings/{finding_id}", {
    params: { path: { finding_id: findingId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listCoverage(
  scanId: string,
  query: { engine?: string; outcome?: string; cursor?: string },
  signal?: Sig,
): Promise<CoveragePage> {
  const { data, error, response } = await api.GET("/v1/scans/{scan_id}/coverage", {
    params: {
      path: { scan_id: scanId },
      query: {
        limit: 200,
        ...(query.engine ? { engine: query.engine } : {}),
        ...(query.outcome ? { outcome: query.outcome } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

// -- Phase 2: issues, comparison, exports, graph ----------------------------------------------

export interface IssueQuery {
  status?: string;
  recheck?: string;
  severity?: string;
  q?: string;
  cursor?: string;
}

export async function listIssues(
  projectId: string,
  query: IssueQuery,
  signal?: Sig,
): Promise<IssuePage> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/issues", {
    params: {
      path: { project_id: projectId },
      query: {
        limit: 50,
        ...(query.status ? { status: [query.status] } : {}),
        ...(query.recheck ? { recheck_state: [query.recheck] } : {}),
        ...(query.severity ? { severity: [query.severity] } : {}),
        ...(query.q ? { q: query.q } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchIssue(issueId: string, signal?: Sig): Promise<IssueDetail> {
  const { data, error, response } = await api.GET("/v1/issues/{issue_id}", {
    params: { path: { issue_id: issueId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function triageIssue(issueId: string, body: IssueTriage): Promise<IssueDetail> {
  const { data, error, response } = await api.PATCH("/v1/issues/{issue_id}", {
    params: { path: { issue_id: issueId } },
    body,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function compareScans(
  scanId: string,
  baseScanId: string,
  signal?: Sig,
): Promise<ScanComparison> {
  const { data, error, response } = await api.GET("/v1/scans/{scan_id}/compare", {
    params: { path: { scan_id: scanId }, query: { base: baseScanId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Same-origin download URL; the session cookie authorizes it. */
export function exportUrl(scanId: string, format: "json" | "sarif"): string {
  return `/v1/scans/${encodeURIComponent(scanId)}/export?format=${format}`;
}

export async function fetchGraphSummary(snapshotId: string, signal?: Sig): Promise<GraphSummary> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/graph", {
    params: { path: { snapshot_id: snapshotId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function searchGraphNodes(
  snapshotId: string,
  query: { q?: string; kind?: string; cursor?: string },
  signal?: Sig,
): Promise<GraphNodePage> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/graph/nodes", {
    params: {
      path: { snapshot_id: snapshotId },
      query: {
        limit: 25,
        ...(query.q ? { q: query.q } : {}),
        ...(query.kind ? { kind: query.kind } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchNeighborhood(
  snapshotId: string,
  nodeId: number,
  depth: number,
  signal?: Sig,
): Promise<GraphNeighborhood> {
  const { data, error, response } = await api.GET(
    "/v1/snapshots/{snapshot_id}/graph/nodes/{node_id}/neighborhood",
    {
      params: { path: { snapshot_id: snapshotId, node_id: nodeId }, query: { depth, limit: 60 } },
      signal: signal ?? null,
    },
  );
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchImpact(
  snapshotId: string,
  nodeId: number,
  depth: number,
  signal?: Sig,
): Promise<GraphImpact> {
  const { data, error, response } = await api.GET(
    "/v1/snapshots/{snapshot_id}/graph/nodes/{node_id}/impact",
    {
      params: { path: { snapshot_id: snapshotId, node_id: nodeId }, query: { depth, limit: 200 } },
      signal: signal ?? null,
    },
  );
  if (data) return data;
  throw toApiError(response, error);
}
