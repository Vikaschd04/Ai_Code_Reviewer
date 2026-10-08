/**
 * Typed API client generated from the committed OpenAPI contract (@crp/contracts).
 * Requests are same-origin; the HttpOnly session cookie carries authentication.
 */
import type { components, paths } from "@crp/contracts";
import createClient from "openapi-fetch";

type Schemas = components["schemas"];
export type ReadinessReport = Schemas["ReadinessReport"];
export type DependencyCheck = Schemas["DependencyCheck"];
export type Capability = Schemas["Capability"];
export type Principal = Schemas["PrincipalResponse"];
export type AuthOptions = Schemas["AuthOptions"];
export type SampleProject = Schemas["SampleProjectResponse"];
export type Project = Schemas["ProjectResponse"];
export type DiagnosticRun = Schemas["DiagnosticRunResponse"];
export type ErrorBody = Schemas["ErrorResponse"];
export type ProjectOverview = Schemas["ProjectOverview"];
export type Intake = Schemas["IntakeResponse"];
export type IntakePolicy = Schemas["IntakePolicyResponse"];
export type Snapshot = Schemas["SnapshotResponse"];
export type FileEntry = Schemas["FileEntryResponse"];
export type FilePage = Schemas["FilePage"];
export type FileContent = Schemas["FileContentResponse"];
export type Scan = Schemas["ScanResponse"];
export type EngineRun = Schemas["EngineRunResponse"];
export type Finding = Schemas["FindingResponse"];
export type FindingPage = Schemas["FindingPage"];
export type FindingDetail = Schemas["FindingDetailResponse"];
export type CoverageRow = Schemas["CoverageRow"];
export type CoveragePage = Schemas["CoveragePage"];
export type Issue = Schemas["IssueResponse"];
export type IssuePage = Schemas["IssuePage"];
export type IssueDetail = Schemas["IssueDetailResponse"];
export type IssueEvent = Schemas["IssueEventResponse"];
export type IssueTriage = Schemas["IssueTriage"];
export type ScanComparison = Schemas["ScanComparison"];
export type ComparisonGroup = Schemas["ComparisonGroup"];
export type ComparisonItem = Schemas["ComparisonItem"];
export type GraphSummary = Schemas["GraphSummary"];
export type GraphNode = Schemas["GraphNodeResponse"];
export type GraphEdge = Schemas["GraphEdgeResponse"];
export type GraphNodePage = Schemas["GraphNodePage"];
export type GraphNeighborhood = Schemas["GraphNeighborhood"];
export type GraphImpact = Schemas["GraphImpact"];
export type FrameworkPack = Schemas["FrameworkPack"];
export type FixOptions = Schemas["FixOptions"];
export type FixProposal = Schemas["FixProposalResponse"];
export type FixValidation = Schemas["FixValidationResponse"];
export type AiStatus = Schemas["AiStatus"];
export type AiPolicy = Schemas["ProjectAiPolicyResponse"];
export type AiRun = Schemas["AiRunResponse"];
export type AiRunCreate = Schemas["AiRunCreate"];
export type AiAnchor = Schemas["AiAnchorResponse"];
export type AiFinding = Schemas["AiFindingResponse"];
export type FixPullRequest = Schemas["FixPullRequestResponse"];
export type GitHubStatus = Schemas["GitHubStatus"];
export type GitHubLinkResult = Schemas["GitHubLinkResult"];
export type GitInstallation = Schemas["GitInstallationResponse"];
export type GitRepository = Schemas["GitRepositoryResponse"];
export type GitConnection = Schemas["GitConnectionResponse"];
export type GitConnectionUpdate = Schemas["GitConnectionUpdate"];
export type CodeReview = Schemas["CodeReviewResponse"];
export type CodeReviewCreate = Schemas["CodeReviewCreate"];
export type Workspace = Schemas["ChangeSetResponse"];
export type AiFixCandidate = Schemas["AiFixCandidateResponse"];
export type WorkspaceListItem = Schemas["ChangeSetListItem"];
export type WorkspaceFile = Schemas["ChangeSetFileSummary"];
export type WorkspaceCheck = Schemas["ChangeSetCheckResponse"];
export type WorkspaceFileContent = Schemas["WorkspaceFileContent"];
export type WorkspaceSaveResult = Schemas["WorkspaceSaveResult"];
export type WorkspaceFixResult = Schemas["WorkspaceFixResult"];
export type WorkspaceIssue = Schemas["WorkspaceIssue"];
export type WorkspaceIssuePage = Schemas["WorkspaceIssuePage"];
export type SnapshotComparison = Schemas["SnapshotComparisonResponse"];
export type FileComparison = Schemas["FileComparison"];

export const api = createClient<paths>({ baseUrl: "", credentials: "same-origin" });

/** A failed API call with the platform's structured error fields. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { code?: unknown }).code === "string" &&
    typeof (value as { message?: unknown }).message === "string"
  );
}

export function toApiError(response: Response, body: unknown): ApiError {
  if (isErrorBody(body)) {
    return new ApiError(response.status, body.code, body.message, body.request_id);
  }
  return new ApiError(
    response.status,
    "unexpected_response",
    `Unexpected response (HTTP ${response.status})`,
    response.headers.get("x-request-id"),
  );
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId ? `${error.message} (request ${error.requestId})` : error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "Request cancelled";
  }
  return "The API could not be reached. Is the development stack running?";
}
