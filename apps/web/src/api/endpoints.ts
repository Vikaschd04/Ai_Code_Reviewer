/** Thin wrappers that turn contract responses into values or ApiError exceptions. */
import {
  api,
  toApiError,
  type AiPolicy,
  type AiRun,
  type ArchitectureMetrics,
  type ArchitectureRules,
  type ArchitectureRulesCheck,
  type NfrAssessment,
  type NfrProfileDocument,
  type AiRunCreate,
  type AiStatus,
  type AuthOptions,
  type CodeReview,
  type CodeReviewCreate,
  type FileComparison,
  type SnapshotComparison,
  type Workspace,
  type WorkspaceCheck,
  type WorkspaceFileContent,
  type WorkspaceFixResult,
  type WorkspaceIssuePage,
  type WorkspaceListItem,
  type WorkspaceSaveResult,
  type FixOptions,
  type FixPullRequest,
  type GitConnection,
  type GitConnectionUpdate,
  type GitHubLinkResult,
  type GitHubStatus,
  type GitInstallation,
  type FixProposal,
  type FixValidation,
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
  engine?: string;
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
        ...(query.engine ? { engine: query.engine } : {}),
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

// -- Phase 3: AI review ----------------------------------------------------------------------

export async function fetchAiStatus(signal?: Sig): Promise<AiStatus> {
  const { data, error, response } = await api.GET("/v1/ai/status", { signal: signal ?? null });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchAiPolicy(projectId: string, signal?: Sig): Promise<AiPolicy> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/ai-policy", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Switch AI review on or off for one project (workspace admins; audited). */
export async function updateAiPolicy(current: AiPolicy, enabled: boolean): Promise<AiPolicy> {
  const { data, error, response } = await api.PUT("/v1/projects/{project_id}/ai-policy", {
    params: { path: { project_id: current.project_id } },
    body: { enabled, max_excerpt_lines: current.max_excerpt_lines, version: current.version },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listAiRuns(
  projectId: string,
  signal?: Sig,
  findingId?: string,
): Promise<AiRun[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/ai-runs", {
    params: {
      path: { project_id: projectId },
      query: { limit: 30, ...(findingId ? { finding_id: findingId } : {}) },
    },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function startAiRun(projectId: string, body: AiRunCreate): Promise<AiRun> {
  const { data, error, response } = await api.POST("/v1/projects/{project_id}/ai-runs", {
    params: { path: { project_id: projectId } },
    body,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchAiRun(runId: string, signal?: Sig): Promise<AiRun> {
  const { data, error, response } = await api.GET("/v1/ai-runs/{run_id}", {
    params: { path: { run_id: runId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function cancelAiRun(runId: string): Promise<AiRun> {
  const { data, error, response } = await api.POST("/v1/ai-runs/{run_id}/cancel", {
    params: { path: { run_id: runId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export function aiExportUrl(runId: string, format: "json" | "sarif"): string {
  return `/v1/ai-runs/${encodeURIComponent(runId)}/export?format=${format}`;
}

// -- Phase 5: validated fixes ----------------------------------------------------------------

export async function fetchFixOptions(findingId: string, signal?: Sig): Promise<FixOptions> {
  const { data, error, response } = await api.GET("/v1/findings/{finding_id}/fix-options", {
    params: { path: { finding_id: findingId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function createFix(findingId: string, recipeId: string): Promise<FixProposal> {
  const { data, error, response } = await api.POST("/v1/findings/{finding_id}/fix-proposals", {
    params: { path: { finding_id: findingId } },
    body: { recipe_id: recipeId },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listFixes(
  projectId: string,
  signal?: Sig,
  findingId?: string,
): Promise<FixProposal[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/fix-proposals", {
    params: {
      path: { project_id: projectId },
      query: { limit: 50, ...(findingId ? { finding_id: findingId } : {}) },
    },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function fetchFix(proposalId: string, signal?: Sig): Promise<FixProposal> {
  const { data, error, response } = await api.GET("/v1/fix-proposals/{proposal_id}", {
    params: { path: { proposal_id: proposalId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function editFix(
  fix: FixProposal,
  edits: { start_line: number; replacement: string[] }[],
): Promise<FixProposal> {
  const { data, error, response } = await api.PUT("/v1/fix-proposals/{proposal_id}/edits", {
    params: { path: { proposal_id: fix.id } },
    body: { version: fix.version, edits },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function rejectFix(proposalId: string, reason: string): Promise<FixProposal> {
  const { data, error, response } = await api.POST("/v1/fix-proposals/{proposal_id}/reject", {
    params: { path: { proposal_id: proposalId } },
    body: { reason },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function validateFix(proposalId: string): Promise<FixValidation> {
  const { data, error, response } = await api.POST("/v1/fix-proposals/{proposal_id}/validations", {
    params: { path: { proposal_id: proposalId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function cancelFixValidation(validationId: string): Promise<FixValidation> {
  const { data, error, response } = await api.POST("/v1/fix-validations/{validation_id}/cancel", {
    params: { path: { validation_id: validationId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export function fixDownloadUrl(proposalId: string, kind: "patch" | "summary"): string {
  return `/v1/fix-proposals/${encodeURIComponent(proposalId)}/${kind}`;
}

/** Open a pull request with a validated fix on the reviewed branch (never merged). */
export async function openFixPullRequest(proposalId: string): Promise<FixPullRequest> {
  const { data, error, response } = await api.POST("/v1/fix-proposals/{proposal_id}/pull-request", {
    params: { path: { proposal_id: proposalId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

// -- Phase 6: GitHub ---------------------------------------------------------------------------

export async function fetchGitHubStatus(signal?: Sig): Promise<GitHubStatus> {
  const { data, error, response } = await api.GET("/v1/github/status", { signal: signal ?? null });
  if (data) return data;
  throw toApiError(response, error);
}

/** Start verified linking: returns the GitHub page where the admin confirms access. */
export async function startGitHubLink(workspaceId: string): Promise<string> {
  const { data, error, response } = await api.POST("/v1/workspaces/{workspace_id}/github/link", {
    params: { path: { workspace_id: workspaceId } },
  });
  if (data) return data.authorize_url;
  throw toApiError(response, error);
}

export async function completeGitHubLink(
  workspaceId: string,
  code: string,
  state: string,
): Promise<GitHubLinkResult> {
  const { data, error, response } = await api.POST(
    "/v1/workspaces/{workspace_id}/github/link/complete",
    { params: { path: { workspace_id: workspaceId } }, body: { code, state } },
  );
  if (data) return data;
  throw toApiError(response, error);
}

export async function listGitInstallations(
  workspaceId: string,
  signal?: Sig,
): Promise<GitInstallation[]> {
  const { data, error, response } = await api.GET(
    "/v1/workspaces/{workspace_id}/github/installations",
    { params: { path: { workspace_id: workspaceId } }, signal: signal ?? null },
  );
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function syncGitInstallation(
  workspaceId: string,
  installationId: string,
): Promise<GitInstallation> {
  const { data, error, response } = await api.POST(
    "/v1/workspaces/{workspace_id}/github/installations/{installation_id}/sync",
    { params: { path: { workspace_id: workspaceId, installation_id: installationId } } },
  );
  if (data) return data;
  throw toApiError(response, error);
}

export async function unlinkGitInstallation(
  workspaceId: string,
  installationId: string,
): Promise<void> {
  const { error, response } = await api.DELETE(
    "/v1/workspaces/{workspace_id}/github/installations/{installation_id}",
    { params: { path: { workspace_id: workspaceId, installation_id: installationId } } },
  );
  if (!response.ok) throw toApiError(response, error);
}

export async function fetchGitConnection(projectId: string, signal?: Sig): Promise<GitConnection> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/git-connection", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function connectRepository(
  projectId: string,
  repositoryId: string,
): Promise<GitConnection> {
  const { data, error, response } = await api.PUT("/v1/projects/{project_id}/git-connection", {
    params: { path: { project_id: projectId } },
    body: { repository_id: repositoryId },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function updateGitConnection(
  projectId: string,
  body: GitConnectionUpdate,
): Promise<GitConnection> {
  const { data, error, response } = await api.PATCH("/v1/projects/{project_id}/git-connection", {
    params: { path: { project_id: projectId } },
    body,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function disconnectRepository(projectId: string): Promise<void> {
  const { error, response } = await api.DELETE("/v1/projects/{project_id}/git-connection", {
    params: { path: { project_id: projectId } },
  });
  if (!response.ok) throw toApiError(response, error);
}

export async function startCodeReview(
  projectId: string,
  body: CodeReviewCreate,
): Promise<CodeReview> {
  const { data, error, response } = await api.POST("/v1/projects/{project_id}/code-reviews", {
    params: { path: { project_id: projectId } },
    body,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listCodeReviews(projectId: string, signal?: Sig): Promise<CodeReview[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/code-reviews", {
    params: { path: { project_id: projectId }, query: { limit: 30 } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function fetchCodeReview(reviewId: string, signal?: Sig): Promise<CodeReview> {
  const { data, error, response } = await api.GET("/v1/code-reviews/{review_id}", {
    params: { path: { review_id: reviewId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function cancelCodeReview(reviewId: string): Promise<CodeReview> {
  const { data, error, response } = await api.POST("/v1/code-reviews/{review_id}/cancel", {
    params: { path: { review_id: reviewId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

// -- Phase 8: fix workspaces ---------------------------------------------------------------------

export async function listWorkspaces(
  projectId: string,
  signal?: Sig,
): Promise<WorkspaceListItem[]> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/change-sets", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

/** Open a workspace on an upload (the latest one when no upload is given). */
export async function createWorkspace(
  projectId: string,
  body: { snapshot_id?: string; title?: string } = {},
): Promise<Workspace> {
  const { data, error, response } = await api.POST("/v1/projects/{project_id}/change-sets", {
    params: { path: { project_id: projectId } },
    body,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function fetchWorkspace(workspaceId: string, signal?: Sig): Promise<Workspace> {
  const { data, error, response } = await api.GET("/v1/change-sets/{change_set_id}", {
    params: { path: { change_set_id: workspaceId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  const { error, response } = await api.DELETE("/v1/change-sets/{change_set_id}", {
    params: { path: { change_set_id: workspaceId } },
  });
  if (!response.ok) throw toApiError(response, error);
}

export async function fetchWorkspaceFile(
  workspaceId: string,
  path: string,
  signal?: Sig,
): Promise<WorkspaceFileContent> {
  const { data, error, response } = await api.GET("/v1/change-sets/{change_set_id}/file", {
    params: { path: { change_set_id: workspaceId }, query: { path } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function saveWorkspaceFile(
  workspace: Workspace,
  path: string,
  content: string,
  findingIds: string[] = [],
): Promise<WorkspaceSaveResult> {
  const { data, error, response } = await api.PUT("/v1/change-sets/{change_set_id}/file", {
    params: { path: { change_set_id: workspace.id } },
    body: { version: workspace.version, path, content, finding_ids: findingIds },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function deleteWorkspaceFile(workspace: Workspace, path: string): Promise<Workspace> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/file/delete", {
    params: { path: { change_set_id: workspace.id } },
    body: { version: workspace.version, path },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function revertWorkspaceFile(workspace: Workspace, path: string): Promise<Workspace> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/file/revert", {
    params: { path: { change_set_id: workspace.id } },
    body: { version: workspace.version, path },
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Apply automatic fixes to the selected findings, or to every finding of one rule. */
export async function applyWorkspaceFixes(
  workspace: Workspace,
  selection: { finding_ids: string[] } | { engine: string; rule_id: string },
): Promise<WorkspaceFixResult> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/fixes", {
    params: { path: { change_set_id: workspace.id } },
    body: { version: workspace.version, ...selection },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export interface WorkspaceIssueQuery {
  outcome?: "fixed" | "still_present" | "suppressed" | "not_rechecked" | "unchecked" | undefined;
  severity?: string | undefined;
  q?: string | undefined;
  fixable?: boolean | undefined;
  cursor?: string | undefined;
}

export async function listWorkspaceIssues(
  workspaceId: string,
  query: WorkspaceIssueQuery,
  signal?: Sig,
): Promise<WorkspaceIssuePage> {
  const { data, error, response } = await api.GET("/v1/change-sets/{change_set_id}/issues", {
    params: {
      path: { change_set_id: workspaceId },
      query: {
        limit: 100,
        ...(query.outcome ? { outcome: query.outcome } : {}),
        ...(query.severity ? { severity: query.severity } : {}),
        ...(query.q ? { q: query.q } : {}),
        ...(query.fixable !== undefined ? { fixable: query.fixable } : {}),
        ...(query.cursor ? { cursor: query.cursor } : {}),
      },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function startWorkspaceCheck(workspaceId: string): Promise<WorkspaceCheck> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/checks", {
    params: { path: { change_set_id: workspaceId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function cancelWorkspaceCheck(checkId: string): Promise<WorkspaceCheck> {
  const { data, error, response } = await api.POST("/v1/change-set-checks/{check_id}/cancel", {
    params: { path: { check_id: checkId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export type WorkspaceExport = "patch" | "mbox" | "changed" | "full" | "summary" | "summary-md";

export function workspaceExportUrl(workspaceId: string, format: WorkspaceExport): string {
  return `/v1/change-sets/${encodeURIComponent(workspaceId)}/export?format=${format}`;
}

export async function compareSnapshots(
  snapshotId: string,
  baseId: string,
  signal?: Sig,
): Promise<SnapshotComparison> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/compare", {
    params: { path: { snapshot_id: snapshotId }, query: { base: baseId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function compareSnapshotFile(
  snapshotId: string,
  baseId: string,
  path: string,
  previousPath: string | null,
  signal?: Sig,
): Promise<FileComparison> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/compare/file", {
    params: {
      path: { snapshot_id: snapshotId },
      query: { base: baseId, path, ...(previousPath ? { previous_path: previousPath } : {}) },
    },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Ask AI for candidate fixes of one finding, made for the file as it is in the workspace. */
export async function requestAiFix(workspaceId: string, findingId: string): Promise<AiRun> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/ai-fixes", {
    params: { path: { change_set_id: workspaceId } },
    body: { finding_id: findingId },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export async function listAiFixes(
  workspaceId: string,
  path: string,
  signal?: Sig,
): Promise<AiRun[]> {
  const { data, error, response } = await api.GET("/v1/change-sets/{change_set_id}/ai-fixes", {
    params: { path: { change_set_id: workspaceId }, query: { path } },
    signal: signal ?? null,
  });
  if (data) return data.items;
  throw toApiError(response, error);
}

export async function applyAiFix(
  workspace: Workspace,
  runId: string,
  candidate: number,
): Promise<WorkspaceSaveResult> {
  const { data, error, response } = await api.POST(
    "/v1/change-sets/{change_set_id}/ai-fixes/{run_id}/apply",
    {
      params: { path: { change_set_id: workspace.id, run_id: runId } },
      body: { version: workspace.version, candidate },
    },
  );
  if (data) return data;
  throw toApiError(response, error);
}

/** Open one pull request with the checked workspace changes (GitHub uploads; never merged). */
export async function openWorkspacePullRequest(
  workspaceId: string,
): Promise<Workspace["pull_request"]["opened"][number]> {
  const { data, error, response } = await api.POST("/v1/change-sets/{change_set_id}/pull-request", {
    params: { path: { change_set_id: workspaceId } },
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Components, structural metrics and cycles of an upload's current architecture map (P10). */
/** NFR readiness: every questionnaire question with evidence, open issues and team answers. */
export async function fetchNfr(projectId: string, signal?: Sig): Promise<NfrAssessment> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/nfr", {
    params: { path: { project_id: projectId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Save the team's NFR profile as a new version (members); ``baseVersion`` guards conflicts. */
export async function saveNfrProfile(
  projectId: string,
  document: NfrProfileDocument,
  baseVersion: number,
  note: string,
): Promise<NfrAssessment> {
  const { data, error, response } = await api.PUT("/v1/projects/{project_id}/nfr/profile", {
    params: { path: { project_id: projectId } },
    body: { document, base_version: baseVersion, note: note.trim() || null },
  });
  if (data) return data;
  throw toApiError(response, error);
}

export function nfrExportUrl(projectId: string, format: "csv" | "md"): string {
  return `/v1/projects/${encodeURIComponent(projectId)}/nfr/export?format=${format}`;
}

/** A project's architecture rules (newest or a given version) and their history. */
export async function fetchArchitectureRules(
  projectId: string,
  signal?: Sig,
  version?: number,
): Promise<ArchitectureRules> {
  const { data, error, response } = await api.GET("/v1/projects/{project_id}/architecture-rules", {
    params: { path: { project_id: projectId }, query: version ? { version } : {} },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Save YAML rules as a new version (members); ``baseVersion`` guards against lost updates. */
export async function saveArchitectureRules(
  projectId: string,
  yaml: string,
  baseVersion: number,
  note: string,
): Promise<ArchitectureRules> {
  const { data, error, response } = await api.PUT("/v1/projects/{project_id}/architecture-rules", {
    params: { path: { project_id: projectId } },
    body: { yaml, base_version: baseVersion, note: note.trim() || null },
  });
  if (data) return data;
  throw toApiError(response, error);
}

/** Check YAML rules (or, without YAML, the saved ones) on an upload; nothing is saved. */
export async function checkArchitectureRules(
  snapshotId: string,
  yaml: string | null,
): Promise<ArchitectureRulesCheck> {
  const { data, error, response } = await api.POST(
    "/v1/snapshots/{snapshot_id}/architecture-rules/check",
    { params: { path: { snapshot_id: snapshotId } }, body: yaml === null ? {} : { yaml } },
  );
  if (data) return data;
  throw toApiError(response, error);
}

export function architectureRulesExportUrl(projectId: string, version?: number): string {
  const query = version ? `?version=${String(version)}` : "";
  return `/v1/projects/${encodeURIComponent(projectId)}/architecture-rules/export${query}`;
}

export async function fetchArchitectureRulesYaml(projectId: string): Promise<string> {
  const response = await fetch(architectureRulesExportUrl(projectId), {
    credentials: "same-origin",
  });
  if (!response.ok) throw toApiError(response, await response.json().catch(() => null));
  return response.text();
}

export async function fetchArchitecture(
  snapshotId: string,
  signal?: Sig,
): Promise<ArchitectureMetrics> {
  const { data, error, response } = await api.GET("/v1/snapshots/{snapshot_id}/architecture", {
    params: { path: { snapshot_id: snapshotId } },
    signal: signal ?? null,
  });
  if (data) return data;
  throw toApiError(response, error);
}
