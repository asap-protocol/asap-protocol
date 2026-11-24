/** TypeScript client for ASAP Protocol (JSON-RPC over HTTP). */
export const SDK_NAME = "@asap-protocol/client" as const;

export {
  AGENT_JWT_TYP,
  HOST_JWT_TYP,
  createAgent,
  createHost,
  jwkThumbprintSha256,
  resumeAgent,
  resumeHost,
} from "./identity.js";
export type { AgentContext, AgentMode, HostContext } from "./identity.js";

export { InMemoryJtiReplayCache, JtiReplayCacheFullError } from "./jti-replay-cache.js";
export type { InMemoryJtiReplayCacheOptions, JtiReplayCache } from "./jti-replay-cache.js";

export { AgentJwtFailureReason } from "./agent-jwt-claims.js";
export type { AgentJwtClaims } from "./agent-jwt-claims.js";
export { verifyAgentJwt } from "./agent-jwt-verify.js";
export type {
  AgentJwtVerifyResult,
  AgentKeyLookup,
  AgentKeyResolver,
  RejectedAgentJwt,
  ResolvedAgentKey,
  VerifiedAgentJwt,
  VerifyAgentJwtOptions,
} from "./agent-jwt-verify.js";

export {
  DEFAULT_REGISTRY_URL,
  WELLKNOWN_MANIFEST_PATH,
  discoverProvider,
  listProviders,
  manifestUrlForBase,
  searchProviders,
} from "./discovery.js";
export type {
  AsapCapabilityBlock,
  AsapManifest,
  AsapSkill,
  DiscoverProviderOptions,
  DiscoveryFetch,
  LiteRegistry,
  ListProvidersOptions,
  RegistryEntry,
  SearchProvidersOptions,
} from "./discovery.js";

export {
  describeCapability,
  executeCapability,
  listCapabilities,
} from "./capabilities.js";
export type {
  CapabilityFetch,
  CapabilityRequestOptions,
  DescribeCapabilityResult,
  ExecuteCapabilityOptions,
  ListCapabilitiesItem,
  ListCapabilitiesOptions,
  ListCapabilitiesResult,
} from "./capabilities.js";

export {
  FatalError,
  JSON_RPC_ASAP_MAX,
  JSON_RPC_ASAP_MIN,
  RecoverableError,
  RemoteFatalRPCError,
  RemoteRecoverableRPCError,
  RPC_AGENT_REVOKED,
  RPC_CIRCUIT_OPEN,
  RPC_CONNECTION_ERROR,
  RPC_HANDLER_NOT_FOUND,
  RPC_INVALID_NONCE,
  RPC_INVALID_STATE,
  RPC_INVALID_TIMESTAMP,
  RPC_MALFORMED_ENVELOPE,
  RPC_REMOTE_GENERIC,
  RPC_RESOURCE_EXHAUSTED,
  RPC_SIGNATURE_VERIFICATION,
  RPC_TASK_ALREADY_COMPLETED,
  RPC_TASK_NOT_FOUND,
  RPC_THREAD_POOL_EXHAUSTED,
  RPC_TIMEOUT,
  RPC_UNSUPPORTED_AUTH_SCHEME,
  RPC_WEBHOOK_URL_REJECTED,
  clampAsapRpcSlot,
  isAsapJsonRpcCode,
  popAsapRemoteErrorMeta,
  remoteRpcErrorFromJson,
  remoteRpcErrorFromJsonRpcError,
} from "./errors.js";
export type {
  AsapRemoteError,
  ConstraintViolation,
  JsonRpcErrorWire,
  PoppedAsapRemoteMeta,
} from "./errors.js";

export { callWithRecoverableRetry } from "./transport.js";
export type { RecoverableRetryOptions } from "./transport.js";

export {
  ASAP_SEND_METHOD,
  ASAP_VERSION_HEADER,
  createAsapStreamClient,
  envelopeFromWireJson,
  streamTaskStreamEnvelopes,
} from "./streaming.js";
export type {
  AsapStreamClient,
  AsapStreamClientConfig,
  StreamFetch,
  StreamTaskStreamEnvelopesOptions,
} from "./streaming.js";

export type {
  AgentURN,
  Envelope,
  EnvelopeFor,
  IsoDateTimeString,
  KnownEnvelope,
  KnownPayloadTypeName,
  PayloadTypeMap,
  TaskCancelPayload,
  TaskMetricsPayload,
  TaskRequestConfig,
  TaskRequestPayload,
  TaskResponsePayload,
  TaskStreamPayload,
  TaskUpdatePayload,
} from "./types/envelope.js";
export { isKnownPayloadType, narrowEnvelope } from "./types/envelope.js";

/** Adapter authoring surface (also available from `@asap-protocol/client/adapters/shared`). */
export type { AsapCapabilityList, AsapExecuteClient } from "./adapters/shared.js";

export type { Storage, WebStorageLike } from "./storage-local.js";
export { MemoryStorage, LocalStorage } from "./storage-local.js";

export {
  agentStatus,
  connectAgent,
  disconnectAgent,
  reactivateAgent,
  requestCapability,
} from "./connection.js";
export type {
  AgentCapabilityGrantSummary,
  AgentLifecycleInfo,
  AgentStatusResult,
  CapabilityRequestSpec,
  ConnectAgentOptions,
  ConnectAgentResult,
  ConnectionFetch,
  ConnectionJwtOptions,
  DisconnectAgentResult,
  ReactivateAgentResult,
  RequestCapabilityResult,
} from "./connection.js";
