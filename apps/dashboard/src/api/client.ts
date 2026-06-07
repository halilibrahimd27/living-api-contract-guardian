import type {
  CampaignRead,
  ChangeReport,
  ContractRead,
  HealthResponse,
  ServiceRead,
} from "./types"

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// Health
export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/healthz")
}

// Services — the API only exposes GET /services/{id} and POST /services.
// The dashboard home page needs a list; we surface a best-effort endpoint
// that the server may or may not support.
export function listServices(): Promise<ServiceRead[]> {
  return apiFetch<ServiceRead[]>("/services")
}

export function getService(id: string): Promise<ServiceRead> {
  return apiFetch<ServiceRead>(`/services/${encodeURIComponent(id)}`)
}

export function getServiceContracts(id: string): Promise<ContractRead[]> {
  return apiFetch<ContractRead[]>(`/services/${encodeURIComponent(id)}/contracts`)
}

// Diff — GET /diff/{id} if supported by the API
export function getDiff(diffId: string): Promise<ChangeReport> {
  return apiFetch<ChangeReport>(`/diff/${encodeURIComponent(diffId)}`)
}

// Campaigns
export function listCampaigns(): Promise<CampaignRead[]> {
  return apiFetch<CampaignRead[]>("/campaigns")
}

export function getCampaign(id: string): Promise<CampaignRead> {
  return apiFetch<CampaignRead>(`/campaigns/${encodeURIComponent(id)}`)
}
